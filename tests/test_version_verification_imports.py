"""Uploaded cuts reach the same timing verification endpoint as previous scans."""

import json

import pytest

from scanner import api
from scanner.db import SavedReviewRow, ScanRow
from scanner.http.identity_verification_api import router as verification_router
from scanner.persistence.repository.version_reuse import source_pending_reuse
from tests.multi_user_access_support import OWNER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import IMPORTS
from tests.test_review_access_http import app as app


@pytest.mark.parametrize("kind", ["edl", "mcf"])
def test_staged_upload_needs_verification_before_creating_a_review(app, monkeypatch, kind):
    app.client.app.include_router(verification_router)
    item_id = app.jellyfin_item(guid("verification-" + kind), LIBRARY_A)
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 60)
    target = {"source": "jellyfin", "item_id": item_id, "media_source_id": None}
    options = {"target": target, "name": "Previous timestamps", "stage_for_verification": True}
    if kind == "mcf":
        options["synchronization"] = {
            "start_seconds": 0,
            "end_seconds": 60,
            "end_mode": "file_end",
        }
    headers = app.accounts.unsafe_cookie(OWNER)
    response = app.client.post(
        f"/api/{kind}-imports/upload",
        headers=headers,
        data={"options": json.dumps(options)},
        files={"file": (f"older.{kind}", IMPORTS[kind])},
    )
    assert response.status_code == 201, response.text
    staged = response.json()
    assert staged["review"] is None
    assert staged["scan"]["reuse_pending"] is True
    scan_id = staged["scan"]["id"]
    version = staged["scan"]["stable_version_id"]
    assert version
    with app.accounts.factory() as db:
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
