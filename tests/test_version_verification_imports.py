"""Uploaded cuts reach the same timing verification endpoint as previous scans."""

import asyncio
import json
from pathlib import Path

import pytest

from scanner import api
from scanner.db import SavedReviewRow, ScanRow
from scanner.http import review_import_routes_api
from scanner.http.identity_verification_api import router as verification_router
from scanner.persistence.repository.media_identity_store import get_version
from scanner.persistence.repository.review_access import ReviewAccess
from scanner.persistence.repository.version_reuse import source_pending_reuse
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import OWNER, USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import IMPORTS
from tests.test_review_access_http import app as app


def _staged_import_options(kind, item_id):
    options = {
        "target": {"source": "jellyfin", "item_id": item_id, "media_source_id": None},
        "name": "Previous timestamps",
        "stage_for_verification": True,
    }
    if kind == "mcf":
        options["synchronization"] = {
            "start_seconds": 0,
            "end_seconds": 60,
            "end_mode": "file_end",
        }
    return options


def _send_staged(app, kind, options, user=OWNER):
    return app.client.post(
        f"/api/{kind}-imports/upload",
        headers=app.accounts.unsafe_cookie(user),
        data={"options": json.dumps(options)},
        files={"file": (f"older.{kind}", IMPORTS[kind])},
    )


@pytest.mark.parametrize(
    ("kind", "denied"),
    [
        ("edl", C.REVIEWS_CREATE_OWN),
        ("mcf", C.REVIEWS_METADATA_EDIT_OWN),
        ("mcf", C.SCANS_FINDINGS_READ),
    ],
)
def test_staged_upload_authorizes_before_hashing(app, monkeypatch, kind, denied):
    item_id = app.jellyfin_item(guid("limited-" + kind), LIBRARY_A)
    options = _staged_import_options(kind, item_id)
    if denied is C.SCANS_FINDINGS_READ:
        scan_id, _ = app.seed_scan(OWNER, "unreadable credits", status="complete")
        options["accepted_credits"] = {
            "scan_id": scan_id,
            "candidate_id": "candidate",
            "accepted_boundary_seconds": 50,
        }
    app.accounts.set_override(USER, denied, OverrideEffect.DENY)
    fingerprint = review_import_routes_api.sampled_media_fingerprint
    reads = []

    def observed_digest(path):
        reads.append(path)
        return fingerprint(path)

    monkeypatch.setattr(review_import_routes_api, "sampled_media_fingerprint", observed_digest)
    response = _send_staged(app, kind, options, USER)
    assert response.status_code in {403, 404}, response.text
    assert reads == []


def test_staged_upload_checks_target_permission_before_revealing_credits_source(app):
    item_id = app.jellyfin_item(guid("limited-credits-mcf"), LIBRARY_A)
    source, _ = app.seed_scan(OWNER, "credits source", status="complete")
    app.accounts.set_override(USER, C.REVIEWS_METADATA_EDIT_OWN, OverrideEffect.DENY)
    options = _staged_import_options("mcf", item_id)
    known, missing = [
        _send_staged(
            app,
            "mcf",
            {
                **options,
                "accepted_credits": {
                    "scan_id": scan_id,
                    "candidate_id": "candidate",
                    "accepted_boundary_seconds": 50,
                },
            },
            USER,
        )
        for scan_id in (source, "missing-scan")
    ]
    assert known.status_code == missing.status_code == 403
    assert known.json() == missing.json()


@pytest.mark.parametrize("kind", ["edl", "mcf"])
def test_staged_upload_rejects_file_replaced_after_hashing(app, monkeypatch, kind):
    item_id = app.jellyfin_item(guid("staged-replacement-" + kind), LIBRARY_A)
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 60)
    create = ReviewAccess.create

    def create_after_replacement(caller, target, operation, **kwargs):
        path = Path(target.resolved_path)
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(b"Different file bytes after the fingerprint")
        replacement.replace(path)
        return create(caller, target, operation, **kwargs)

    monkeypatch.setattr(ReviewAccess, "create", create_after_replacement)
    response = _send_staged(app, kind, _staged_import_options(kind, item_id))
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "target_changed"
    with app.accounts.factory() as db:
        assert db.query(ScanRow).count() == 0
        assert db.query(SavedReviewRow).count() == 0


@pytest.mark.parametrize("kind", ["edl", "mcf"])
def test_staged_upload_needs_verification_before_creating_a_review(app, monkeypatch, kind):
    app.client.app.include_router(verification_router)
    item_id = app.jellyfin_item(guid("verification-" + kind), LIBRARY_A)
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 60)
    options = _staged_import_options(kind, item_id)
    target = options["target"]
    fingerprint = review_import_routes_api.sampled_media_fingerprint
    hashed = False

    def offloaded_digest(path):
        nonlocal hashed
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        hashed = True
        return fingerprint(path)

    monkeypatch.setattr(review_import_routes_api, "sampled_media_fingerprint", offloaded_digest)
    headers = app.accounts.unsafe_cookie(OWNER)
    response = _send_staged(app, kind, options)
    assert response.status_code == 201, response.text
    assert hashed
    staged = response.json()
    assert staged["review"] is None
    assert staged["scan"]["reuse_pending"] is True
    scan_id = staged["scan"]["id"]
    version = staged["scan"]["stable_version_id"]
    assert version
    with app.accounts.factory() as db:
        saved_version = get_version(db, version)
        assert saved_version.sampled_md5 and saved_version.content_sha256 is None
        assert source_pending_reuse(db.get(ScanRow, scan_id))
        assert db.query(SavedReviewRow).count() == 0

    # MCF includes a both-channel finding. Use its video projection, so no real
    # audio stream needs to exist in the stub file used by this HTTP fixture.
    payload = {
        "source_scan_id": scan_id,
        "target": target,
        "channels": ["video"],
        "method": "original",
    }
    if not staged["scan"]["scan_video"]:
        assert kind == "edl"
        return

    path = f"/api/media-versions/{version}/reuse"
    response = app.client.post(f"{path}/preview", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["token"]
    response = app.client.post(
        f"{path}/approve",
        headers=headers,
        json={**payload, "token": plan["token"], "confirmed": True},
    )
    assert response.status_code == 200, response.text
    derived = response.json()["scan"]
    assert derived["id"] != scan_id
    assert derived["reuse_pending"] is False
    assert derived["stable_version_id"] == version
    with app.accounts.factory() as db:
        assert source_pending_reuse(db.get(ScanRow, scan_id))


def test_same_jellyfin_id_with_conflicting_metadata_cannot_register_a_target(app):
    from scanner.http.stable_catalog_api import router as stable_router
    from scanner.modeling.media_identity import normalize_jellyfin
    from scanner.persistence.repository.media_identity_store import observe_title

    app.client.app.include_router(stable_router)
    scan_id, _ = app.seed_scan(OWNER, "Original work", status="complete")
    with app.accounts.factory() as db:
        source = db.get(ScanRow, scan_id)
        item_id = source.media_item.jellyfin_id
        original = normalize_jellyfin({"Type": "Movie", "ProviderIds": {"Tmdb": "110"}})
        title_id = observe_title(db, source.media_item, original)
        db.commit()

    replacement = normalize_jellyfin({"Type": "Movie", "ProviderIds": {"Tmdb": "720"}})
    app.jellyfin_item(item_id, LIBRARY_A)
    app.items[item_id] = app.items[item_id].model_copy(update={"media_identity": replacement})
    response = app.client.post(
        f"/api/editarr-items/{title_id}/versions",
        headers=app.accounts.unsafe_cookie(OWNER),
        json={"item_id": item_id, "media_source_id": None},
    )
    assert response.status_code == 409, response.text
    assert "conflict" in response.json()["detail"]
