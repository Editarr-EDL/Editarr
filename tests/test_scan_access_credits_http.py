"""Credits detection jobs follow the same own/other rules over real HTTP (MU-21)."""

from __future__ import annotations

import pytest

from scanner.credits.credits_detector import default_configuration
from scanner.db import ScanRow
from scanner.models import ScanMode
from scanner.persistence.repository.activity_ownership import ActivityOwner
from scanner.repository import create_scan, enqueue_scan, upsert_media_item
from tests.multi_user_access_support import ADMIN, PEER_USER, USER, live_accounts
from tests.scan_access_http import JELLYFIN, LIBRARY_A, guid, serve_scans

QWEN = "qwen3-vl-4b-instruct-4bit"


@pytest.fixture
def app(monkeypatch, tmp_path):
    accounts = live_accounts(tmp_path)
    served = serve_scans(monkeypatch, accounts, tmp_path)
    yield served
    served.client.close()
    accounts.close()


def _waiting_credits_job(app, initiator: int) -> str:
    path = str(app.file("credits.mkv").resolve())
    with app.accounts.factory() as db:
        media = upsert_media_item(
            db,
            server_id=JELLYFIN,
            jellyfin_id=guid("credits"),
            library_id=LIBRARY_A,
            item_type="Movie",
            name="Credits",
            path=path,
        )
        scan = create_scan(
            db,
            media_item=media,
            resolved_local_path=path,
            source_path=path,
            name="End Credits detection",
            scan_audio=False,
            audio_model=None,
            audio_filter=None,
            scan_video=True,
            video_model=None,
            scan_mode=ScanMode.CREDITS_DETECTION,
            credits_detection_configuration=default_configuration(qwen_model=QWEN),
            initial_status="awaiting_model_consent",
            initiator=ActivityOwner(initiator, app.revision(initiator)),
        )
        enqueue_scan(db, scan.id)
        return scan.id


def _consent(app, user_id: int, scan_id: str):
    return app.send(
        "POST",
        f"/api/credits-detections/{scan_id}/model-consent",
        app.accounts.unsafe_cookie(user_id),
        {"decision": "continue_without_qwen"},
    )


def test_only_the_initiator_or_an_admin_answers_a_waiting_credits_job(app):
    scan_id = _waiting_credits_job(app, USER)

    hidden = app.send("GET", f"/api/credits-detections/{scan_id}", app.accounts.key(PEER_USER))
    assert hidden.status_code == 404
    refused = _consent(app, PEER_USER, scan_id)
    assert refused.status_code == 404
    assert refused.json()["detail"]["code"] == "scan_not_found"
    assert app.scan(scan_id).status == "awaiting_model_consent"

    own = _consent(app, USER, scan_id)
    assert own.status_code == 202, own.text
    assert app.scan(scan_id).status == "queued"
    assert app.calls == ["wake"]


def test_consent_needs_model_download_permission(app):
    from scanner.security.access.capability_ids import Capability as C
    from scanner.security.access.overrides import OverrideEffect

    scan_id = _waiting_credits_job(app, USER)
    app.accounts.set_override(USER, C.MODELS_DOWNLOAD, OverrideEffect.DENY)

    refused = _consent(app, USER, scan_id)

    assert refused.status_code == 403
    assert refused.json()["code"] == "capability_denied"
    assert app.scan(scan_id).status == "awaiting_model_consent"
    admin = _consent(app, ADMIN, scan_id)
    assert admin.status_code == 202, admin.text
    row = app.scan(scan_id)
    assert isinstance(row, ScanRow) and row.initiator_id == USER


def test_a_server_path_is_never_a_credits_target(app):
    from fastapi.exceptions import RequestValidationError

    import scanner.api as root

    app.client.app.add_exception_handler(RequestValidationError, root.scan_name_validation_error)
    video = str(app.file("credits-target.mkv").resolve())
    for actor in (USER, ADMIN):
        for path in (video, "/missing/never-probed.mkv"):
            refused = app.send(
                "POST",
                "/api/credits-detections",
                app.accounts.unsafe_cookie(actor),
                {"target": {"source": "manual", "video_path": path}},
            )
            assert refused.status_code == 400
            assert refused.json()["detail"]["code"] == "invalid_credits_target"
            assert path not in refused.text
    assert app.queued() == []
