"""VideoSkip uploads follow Editarr's authorization and timing verification flow."""

import json
from hashlib import sha256

from scanner import api
from scanner.db import SavedReviewRow, ScanFindingRow, ScanRow
from scanner.http.identity_verification_api import router as verification_router
from scanner.http.videoskip_import_api import router
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import OWNER, USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import app as app
from tests.test_videoskip import SKP


def _upload(app, item_id, *, content=SKP, owner=OWNER, **updates):
    target = {"source": "jellyfin", "item_id": item_id, "media_source_id": None}
    options = {
        "name": "Example VideoSkip",
        "target": target,
        "attribution": {
            "source_url": "https://videoskip.herokuapp.com/exchange/skip/54/",
            "uploader": "Contributor",
        },
        **updates,
    }
    return app.client.post(
        "/api/videoskip-imports/upload",
        headers=app.accounts.unsafe_cookie(owner),
        data={"options": json.dumps(options)},
        files={"file": ("Example [netflix-local].skp", content)},
    )


def test_upload_stages_cues_and_keeps_metadata_after_approval(app, monkeypatch):
    app.client.app.include_router(router)
    app.client.app.include_router(verification_router)
    item_id = app.jellyfin_item(guid("videoskip-upload"), LIBRARY_A)
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 60)
    response = _upload(app, item_id)
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["review"] is None
    assert payload["issue_count"] == 4
    scan = payload["scan"]
    assert scan["reuse_pending"] and scan["scan_audio"] and scan["scan_video"]
    assert scan["duration_seconds"] is None
    assert scan["finding_count"] == 2
    source = scan["videoskip_source"]
    assert source["uploader"] == "Contributor"
    assert source["source_url"].endswith("/54/")
    assert source["filename"] == "Example [netflix-local].skp"
    assert source["sha256"] == sha256(SKP).hexdigest()
    assert source["sync"]["reference_seconds"] == 5.25
    assert source["sync"]["platform_offsets_seconds"] == {"netflix": 0, "local": -0.25}
    with app.accounts.factory() as db:
        assert db.query(SavedReviewRow).count() == 0
        cues = db.query(ScanFindingRow).filter_by(scan_id=scan["id"]).all()
        assert sorted((cue.start_seconds, cue.edit_action) for cue in cues) == [
            (10.2, "mute"), (15, "skip"),
        ]

    request = {
        "source_scan_id": scan["id"],
        "target": {"source": "jellyfin", "item_id": item_id, "media_source_id": None},
        "channels": ["video"],
        "method": "original",
    }
    headers = app.accounts.unsafe_cookie(OWNER)
    path = f"/api/media-versions/{scan['stable_version_id']}/reuse"
    preview = app.client.post(f"{path}/preview", headers=headers, json=request)
    assert preview.status_code == 200, preview.text
    approved = app.client.post(
        f"{path}/approve",
        headers=headers,
        json={**request, "token": preview.json()["token"], "confirmed": True},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["scan"]["videoskip_source"] == source
    assert not approved.json()["scan"]["reuse_pending"]


def test_refusals_do_not_create_scans(app):
    app.client.app.include_router(router)
    item = app.jellyfin_item(guid("unusable-videoskip"), LIBRARY_A)
    app.accounts.set_override(USER, C.REVIEWS_EVIDENCE_EDIT_OWN, OverrideEffect.DENY)
    denied = _upload(app, item, owner=USER)
    assert denied.status_code == 403, denied.text
    disabled = _upload(app, item, stage_for_verification=False)
    assert disabled.status_code == 422, disabled.text
    unsupported = _upload(app, item, content=b"0:00:05 --> 0:00:06\nsex blur 3\n")
    assert unsupported.status_code == 422, unsupported.text
    assert unsupported.json()["detail"]["code"] == "no_supported_videoskip_actions"
    with app.accounts.factory() as db:
        assert db.query(ScanRow).count() == db.query(SavedReviewRow).count() == 0
