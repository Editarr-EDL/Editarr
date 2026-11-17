"""Nearest-rounded historical cues reconcile to an immutable outward source floor."""

import copy
import json
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import pytest

import scanner.api as root
from scanner import repository
from scanner.db import (
    ReviewFilterRow,
    ReviewPolicyEvidenceRow,
    ScanFindingRow,
    ScanRow,
    UserRow,
)
from scanner.mcf import TimingInterval, map_affine_range
from scanner.security import account_repository as accounts
from scanner.security.auth_repository import issue_session
from tests.arr_archive_support import undo_archive_migration
from tests.filter_policy_http_support import rule
from tests.multi_user_access_support import USER
from tests.owner_schema_support import (
    undo_explicit_filter_modes_migration,
    undo_jellyfin_admission_migration,
    undo_jellyfin_owner_migration,
    undo_manual_filter_tracking_migration,
    undo_remove_passwords_migration,
)
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_policy_binding import body
from tests.test_review_policy_producers import _assert_export_coverage
from tests.test_review_policy_saved_floor import app as app
from tests.test_review_policy_saved_floor import exports
from tests.test_review_policy_view import enforce

WINDOWS = [(2.5, 5397.25), (12.3456789, 5412.3456789), (0, 5400 * 24000 / 25025)]
SOURCE = (1234.567, 1238.901)


def upload(app, monkeypatch, window, *, outside=False):
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 5420.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 5420.0)
    item_id = app.jellyfin_item(guid("roundoff"), LIBRARY_A)
    content = (
        "WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nTITLE Movie\nYEAR 2001\nTYPE movie\n\n"
        "NOTE\nSTART 00:00:00.000\nEND 01:30:00.000\n\n"
        "00:20:34.567 --> 00:20:38.901\nswearing=low=audio\n\n"
        + ("01:30:10.000 --> 01:30:12.000\nswearing=low=audio\n" if outside else "")
    ).encode()
    options = {
        "target": {"source": "jellyfin", "item_id": item_id},
        "name": "Roundoff",
        "synchronization": {
            "start_seconds": window[0],
            "end_seconds": window[1],
            "end_mode": "before_credits",
        },
        "mcf_material_end_seconds": window[1],
    }
    app.client.cookies.clear()
    return app.client.post(
        "/api/mcf-imports/upload",
        headers=app.accounts.unsafe_cookie(USER),
        data={"options": json.dumps(options)},
        files={"file": ("input.mcf", content)},
    )


def retime(app, detail, window):
    draft = copy.deepcopy(body(detail))
    draft.pop("name")
    synchronization = draft.pop("synchronization")
    draft.update(
        current_synchronization=synchronization,
        proposed_synchronization=synchronization
        | {
            "start_seconds": window[0],
            "end_seconds": window[1],
        },
        new_manual_filters=[],
        filter_basis="base",
    )
    return app.send(
        "POST",
        f"/api/reviews/{detail['id']}/retime",
        app.accounts.unsafe_cookie(USER),
        draft,
    )


def assert_enforced_roundtrip(app, detail):
    enforce(app.accounts, rules=[rule("deny", evidence_source="audio")])
    route = f"/api/reviews/{detail['id']}"
    detail = app.send("GET", route, app.accounts.cookie(USER)).json()
    assert not detail["policy_missing_evidence"]
    with app.accounts.factory() as db:
        media = Path(db.get(ScanRow, detail["memberships"][0]["scan_id"]).resolved_local_path)
    window = tuple(detail["synchronization"][field] for field in ("start_seconds", "end_seconds"))
    _assert_export_coverage(exports(app, route, media, detail), window, (0, 5400), SOURCE)
    saved = app.send("PUT", route, app.accounts.unsafe_cookie(USER), body(detail))
    assert saved.status_code == 200, saved.text
    with app.accounts.factory() as db:
        stored = db.query(ReviewFilterRow).filter_by(review_id=detail["id"]).one()
        assert (stored.start_seconds, stored.end_seconds) == tuple(
            detail["filters"][0][field] for field in ("start_seconds", "end_seconds")
        )
        for endpoint, original, lower in zip(
            (stored.start_seconds, stored.end_seconds), SOURCE, (True, False)
        ):
            exact = (
                Fraction(window[0])
                + Fraction(original) * (Fraction(window[1]) - Fraction(window[0])) / 5400
            )
            assert Fraction(endpoint) <= exact if lower else Fraction(endpoint) >= exact


@pytest.mark.parametrize("window", WINDOWS)
def test_head_optional_retime_then_enforcement(app, monkeypatch, window):
    imported = upload(app, monkeypatch, WINDOWS[0])
    assert imported.status_code == 201, imported.text
    detail = imported.json()["review"]
    mapped = retime(app, detail, window)
    assert mapped.status_code == 200, mapped.text
    draft = (
        body(detail)
        | {
            key: mapped.json()[key]
            for key in (
                "synchronization",
                "mcf_document",
                "accepted_credits",
            )
        }
        | {"filters": mapped.json()["base_filters"], "filter_basis": "base"}
    )
    saved = app.send("PUT", f"/api/reviews/{detail['id']}", app.accounts.unsafe_cookie(USER), draft)
    assert saved.status_code == 200, saved.text
    assert_enforced_roundtrip(app, saved.json())


@pytest.mark.parametrize("window", WINDOWS)
def test_legacy_nearest_import_cues_under_source_basis_binding(app, monkeypatch, window):
    from scanner.persistence.migrations import adoption

    imported = upload(app, monkeypatch, window)
    assert imported.status_code == 201, imported.text
    detail = imported.json()["review"]
    with app.accounts.factory() as db:
        stored = db.query(ReviewFilterRow).filter_by(review_id=detail["id"]).one()
        finding = db.get(ScanFindingRow, stored.source_finding_id)
        stored.start_seconds, stored.end_seconds = map_affine_range(
            finding.start_seconds,
            finding.end_seconds,
            TimingInterval(0, 5400),
            TimingInterval(*window),
        )
        db.commit()
        database = Path(db.bind.url.database)
        for column in (
            "intended_policy_owner_id",
            "content_bytes",
            "snapshot_json",
            "destination_path",
            "publication_revision",
        ):
            db.connection().exec_driver_sql(f"ALTER TABLE media_item_exports DROP COLUMN {column}")
        db.connection().exec_driver_sql("PRAGMA foreign_keys = OFF")
        undo_manual_filter_tracking_migration(db.connection())
        undo_explicit_filter_modes_migration(db.connection())
        undo_remove_passwords_migration(db.connection())
        db.connection().exec_driver_sql("DROP TABLE jellyfin_user_blocks")
        undo_archive_migration(db.connection())
        undo_jellyfin_admission_migration(db.connection())
        undo_jellyfin_owner_migration(db.connection())
        db.connection().exec_driver_sql("DROP TABLE account_transfer_moves")
        db.connection().exec_driver_sql("DROP TABLE account_dispositions")
        db.connection().exec_driver_sql("DROP TABLE account_link_export_moves")
        db.connection().exec_driver_sql("DROP TABLE account_link_transitions")
        db.connection().exec_driver_sql("DROP TABLE media_item_export_regenerations")
        db.connection().exec_driver_sql("DROP TABLE review_scan_repairs")
        db.connection().exec_driver_sql("DROP TABLE review_policy_evidence")
        db.connection().exec_driver_sql(
            "UPDATE alembic_version SET version_num = '0018_jellyfin_account_sync'"
        )
        db.commit()
    generation = adoption.upgrade_existing_database(database, database.parent)
    assert generation.is_dir()
    recovered = adoption.export_upgrade_recovery(
        database, database.parent, database.parent / "old.db"
    )
    assert adoption._inspect_database(recovered) == "0018_jellyfin_account_sync"
    with app.accounts.factory() as db:
        binding = db.query(ReviewPolicyEvidenceRow).filter_by(review_id=detail["id"]).one()
        assert (
            binding.start_seconds,
            binding.end_seconds,
            binding.sync_start,
            binding.sync_end,
        ) == (
            *SOURCE,
            0,
            5400,
        )
        member = db.get(UserRow, USER)
        assert (member.status, member.disable_cause) == ("disabled", "unlinked")
        # The member has no Jellyfin link, and 0030 revoked its local-password session;
        # enable it in place and sign it in again so its review is readable again.
        accounts.set_disable_cause(db, USER, None, now=datetime.now(timezone.utc))
        db.commit()
        app.accounts.sessions[USER] = issue_session(
            db, remember_me=False, user_id=USER, configuration=app.accounts.configuration
        )
    assert_enforced_roundtrip(app, detail)


@pytest.mark.parametrize("enforced", [False, True])
def test_out_of_runtime_retime_keeps_422_and_filter_ids(app, monkeypatch, enforced):
    imported = upload(app, monkeypatch, (0, 5400), outside=True)
    assert imported.status_code == 201, imported.text
    detail = imported.json()["review"]
    if enforced:
        enforce(app.accounts, rules=[rule("deny", evidence_source="audio")])
        detail = app.send("GET", f"/api/reviews/{detail['id']}", app.accounts.cookie(USER)).json()
    refused = retime(app, detail, (200, 5420))
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "mapped_cue_out_of_bounds"
    assert refused.json()["detail"]["filter_ids"] == [detail["filters"][1]["id"]]


def test_optional_out_of_runtime_import_keeps_original_message(app, monkeypatch):
    refused = upload(app, monkeypatch, (0, 5420), outside=True)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "mapped_cue_out_of_bounds"
    assert refused.json()["detail"]["message"] == "Mapped cue endpoint is outside the media runtime"
