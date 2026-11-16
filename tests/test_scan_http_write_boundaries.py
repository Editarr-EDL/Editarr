"""Provider delays in scan HTTP requests leave unrelated writes available."""

import time
from dataclasses import replace
from threading import Thread

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import scanner.api as root
from scanner import repository
from scanner.db import MediaItemRow, ScanRow
from scanner.http import scan_access
from scanner.integrations.jellyfin_catalog import parse_catalog
from scanner.persistence.rows.db_auth import AccountSettingsRow
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from scanner.security.jellyfin_accounts import reads, store
from scanner.security.jellyfin_accounts.evidence import evidence_cache
from scanner.security.scan_jobs import media
from tests.media_access_support import (
    FAMILY,
    FAMILY_FILM,
    JELLYFIN_ITEMS,
    jellyfin_user,
    listing,
    record,
    resolve,
)
from tests.multi_user_access_support import USER
from tests.scan_access_http import JELLYFIN, serve_scans
from tests.test_jellyfin_account_evidence import ScriptedAdapter
from tests.test_jellyfin_account_evidence import clock as clock
from tests.test_jellyfin_account_evidence import linked as linked
from tests.test_scan_access_credits_http import _waiting_credits_job


@pytest.fixture
def submission_app(linked, clock, monkeypatch, tmp_path):
    app = serve_scans(monkeypatch, linked, tmp_path)
    app.jellyfin_item(FAMILY_FILM, FAMILY)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(media, "_resolver", lambda _db: resolve)
    monkeypatch.setattr(evidence_cache, "_copies", {})
    monkeypatch.setattr(evidence_cache, "_items", {})

    class FixedDatetime:
        @staticmethod
        def now(_zone):
            return clock()

    monkeypatch.setattr(scan_access, "datetime", FixedDatetime)
    yield app
    app.client.close()


def submission(app, kind):
    """Build one actual HTTP mutation with authorized synthetic media."""
    options = {"scan_audio": False, "scan_video": True}
    if kind == "credits":
        return (
            "POST",
            "/api/credits-detections",
            {"target": {"source": "jellyfin", "item_id": FAMILY_FILM}},
            202,
        )
    if kind == "ordinary":
        return "POST", "/api/scans", {"jellyfin_item_id": FAMILY_FILM, **options}, 201
    if kind == "consent":
        scan_id = _waiting_credits_job(app, USER)
    else:
        scan_id, queue_id = app.seed_scan(
            USER, kind, library=FAMILY, status="orphaned" if kind == "resume" else "queued"
        )
    with app.accounts.factory() as db:
        scan = db.get(ScanRow, scan_id)
        scan.resolved_local_path = app.items[FAMILY_FILM].path
        item = db.get(MediaItemRow, scan.media_item_id)
        item.jellyfin_id, item.library_id = FAMILY_FILM, FAMILY
        db.commit()
    if kind == "consent":
        return (
            "POST",
            f"/api/credits-detections/{scan_id}/model-consent",
            {"decision": "continue_without_qwen"},
            202,
        )
    if kind == "reorder":
        app.accounts.set_override(USER, C.SCAN_JOBS_REORDER, OverrideEffect.ALLOW)
        return "POST", "/api/scans/queue/reorder", {"queue_ids": [queue_id]}, 200
    if kind == "cancel":
        return "DELETE", f"/api/scans/queue/{queue_id}", None, 200
    if kind == "resume":
        return "POST", f"/api/scans/queue/{queue_id}/resume", None, 200
    if kind == "rename":
        return "PATCH", f"/api/scans/{scan_id}", {"name": "Renamed"}, 200
    if kind == "referenced-delete":
        app.seed_review(USER, "reference", scan_id)
        return "DELETE", f"/api/scans/{scan_id}", None, 409
    return "DELETE", f"/api/scans/{scan_id}", None, 200


@pytest.mark.parametrize(
    "kind",
    [
        "ordinary",
        "credits",
        "consent",
        "reorder",
        "cancel",
        "resume",
        "rename",
        "delete",
        "referenced-delete",
    ],
)
def test_slow_submission_leaves_an_unrelated_writer_free(submission_app, clock, monkeypatch, kind):
    app = submission_app
    method, path, body, expected = submission(app, kind)
    outcome = []

    def write():
        try:
            with submission_app.accounts.factory() as db:
                db.execute(text("UPDATE users SET id = id WHERE id = 1"))
                db.commit()
            outcome.append("committed")
        except OperationalError as error:
            outcome.append(str(error.orig))

    class SlowAdapter(ScriptedAdapter):
        def catalog(self, server):
            item = JELLYFIN_ITEMS[FAMILY_FILM].model_copy(
                update={"path": app.items[FAMILY_FILM].path}
            )
            return parse_catalog([listing([record(item)])], server, clock())

        def item_access(self, identity, item_ids):
            if not outcome:
                writer = Thread(target=write)
                writer.start()
                time.sleep(5.5)
                writer.join(timeout=10)
                assert not writer.is_alive()
            return super().item_access(identity, item_ids)

    monkeypatch.setattr(evidence_cache, "adapters", lambda _db: SlowAdapter(clock))
    response = app.send(method, path, app.accounts.unsafe_cookie(USER), body)
    assert response.status_code == expected, response.text
    assert outcome == ["committed"]


@pytest.mark.parametrize("change", ["permission", "snapshot", "generation", "fence", "epoch"])
def test_credits_rechecks_stored_authority_before_persisting(
    submission_app, clock, monkeypatch, change
):
    app = submission_app
    calls = []

    class RacingAdapter(ScriptedAdapter):
        def item_access(self, identity, item_ids):
            calls.append("item")
            if len(calls) == 1:
                if change == "permission":
                    app.accounts.set_override(USER, C.SCANS_START, OverrideEffect.DENY)
                elif change == "snapshot":
                    user = jellyfin_user("Casey")
                    restricted = replace(
                        user,
                        media=replace(user.media, all_libraries=False, library_ids=frozenset()),
                    )
                    with app.accounts.factory() as db:
                        store.apply(db, USER, restricted, reads.begin_read(db, clock))
                else:
                    with app.accounts.factory() as db:
                        settings = db.get(AccountSettingsRow, 1)
                        if change == "generation":
                            settings.jellyfin_read_generation += 1
                        elif change == "fence":
                            settings.jellyfin_clock_fence += 1
                        else:
                            settings.jellyfin_read_epoch = "f" * 32
                        db.commit()
            answer = super().item_access(identity, item_ids)
            if change == "snapshot" and len(calls) > 1:
                from scanner.integrations.jellyfin_user_snapshot import ItemAccess

                return replace(answer, decisions={item: ItemAccess.RESTRICTED for item in item_ids})
            return answer

    monkeypatch.setattr(evidence_cache, "adapters", lambda _db: RacingAdapter(clock))
    method, path, body, _expected = submission(app, "credits")
    response = app.send(method, path, app.accounts.unsafe_cookie(USER), body)
    if change in {"permission", "snapshot"}:
        assert response.status_code in {403, 404}, response.text
        assert app.queued() == []
        with app.accounts.factory() as db:
            assert db.query(ScanRow).count() == 0
            assert db.query(MediaItemRow).count() == 0
    else:
        assert response.status_code == 202, response.text
        assert len(app.queued()) == 1
        assert len(calls) >= 2


def test_credits_persists_the_target_that_was_authorized(submission_app, clock, monkeypatch):
    app = submission_app
    old_path = app.items[FAMILY_FILM].path
    new_path = str(app.file("replacement.mkv"))
    with app.accounts.factory() as db:
        target = repository.upsert_media_item(
            db,
            server_id=JELLYFIN,
            jellyfin_id=FAMILY_FILM,
            library_id=FAMILY,
            item_type="Movie",
            name="Original",
            path=old_path,
        )
        media_id = target.id

    class RacingAdapter(ScriptedAdapter):
        def catalog(self, server):
            item = JELLYFIN_ITEMS[FAMILY_FILM].model_copy(update={"path": old_path})
            return parse_catalog([listing([record(item)])], server, clock())

        def item_access(self, identity, item_ids):
            with app.accounts.factory() as db:
                db.get(MediaItemRow, media_id).path = new_path
                db.commit()
            return super().item_access(identity, item_ids)

    monkeypatch.setattr(evidence_cache, "adapters", lambda _db: RacingAdapter(clock))
    response = app.send(
        "POST",
        "/api/credits-detections",
        app.accounts.unsafe_cookie(USER),
        {"target": {"source": "jellyfin", "item_id": FAMILY_FILM}},
    )
    assert response.status_code == 202, response.text
    scan = app.scan(response.json()["scan"]["id"])
    assert scan.source_path == scan.resolved_local_path == old_path
    assert scan.media_item_id == media_id
