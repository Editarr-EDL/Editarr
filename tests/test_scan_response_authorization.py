"""Scan visibility never implies permission for findings, paths, or review metadata."""

import scanner.api as root
from scanner import repository
from scanner.db import ScanRow
from scanner.models import ReviewCreate, ReviewScanMembership
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import OWNER, PEER_ADMIN, USER
from tests.scan_access_http import JELLYFIN, LIBRARY_A, LIBRARY_B, guid
from tests.scan_job_boundary_support import finding
from tests.test_scan_access_http import app as app


def test_references_use_the_reviews_target_not_the_source_scan(app):
    scan_id, _ = app.seed_scan(USER, "shared-source", library=LIBRARY_A, status="complete")
    with app.accounts.factory() as db:
        target = repository.upsert_media_item(
            db,
            server_id=JELLYFIN,
            jellyfin_id="3" * 32,
            library_id=LIBRARY_B,
            item_type="Movie",
            name="Restricted target",
            path=app.scan(scan_id).resolved_local_path,
        )
        repository.associate_scans_with_media_item(db, media_item_id=target.id, scan_ids=[scan_id])
        review = repository.create_saved_review(
            db,
            media_item_id=target.id,
            media_source_id=None,
            duration_seconds=1,
            data=ReviewCreate(
                name="Private library review",
                memberships=[ReviewScanMembership(scan_id=scan_id, role="video")],
            ),
        )
    app.limit_to_library(PEER_ADMIN, LIBRARY_A)
    visible = app.send("GET", f"/api/scans/{scan_id}/references", app.accounts.key(PEER_ADMIN))
    refused = app.send("DELETE", f"/api/scans/{scan_id}", app.accounts.unsafe_cookie(PEER_ADMIN))
    assert visible.status_code == 200 and visible.json()["reviews"] == []
    assert refused.status_code == 409 and app.scan(scan_id) is not None
    assert review.id not in refused.text and review.name not in refused.text
    owner = app.send("GET", f"/api/scans/{scan_id}/references", app.accounts.key(OWNER))
    assert owner.status_code == 200 and owner.json()["reviews"][0]["id"] == review.id


def test_findings_denial_blocks_actual_saved_content(app):
    scan_id, _ = app.seed_scan(USER, "findings-authority", status="complete")
    with app.accounts.factory() as db:
        repository.create_scan_findings_batch(db, scan_id, [finding(scan_id)])
    allowed = app.send("GET", f"/api/scans/{scan_id}/findings", app.accounts.key(USER))
    assert allowed.status_code == 200 and "Synthetic finding" in allowed.text
    app.accounts.set_override(USER, C.SCANS_FINDINGS_READ, OverrideEffect.DENY)
    denied = app.send("GET", f"/api/scans/{scan_id}/findings", app.accounts.key(USER))
    assert denied.status_code == 403 and "Synthetic finding" not in denied.text


def test_shared_credits_status_hides_the_owners_path(app, monkeypatch):
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    item_id = app.jellyfin_item(guid("owner-private-path"), LIBRARY_A)
    path = app.items[item_id].path
    started = app.send(
        "POST",
        "/api/credits-detections",
        app.accounts.unsafe_cookie(OWNER),
        {"target": {"source": "jellyfin", "item_id": item_id}},
    )
    assert started.status_code == 202, started.text
    scan_id = started.json()["scan"]["id"]
    with app.accounts.factory() as db:
        db.get(ScanRow, scan_id).status = "complete"
        db.commit()
    history = app.send("GET", "/api/scans/history", app.accounts.key(USER))
    status = app.send("GET", f"/api/credits-detections/{scan_id}", app.accounts.key(USER))
    assert history.status_code == status.status_code == 200
    assert scan_id in history.text and path not in history.text and path not in status.text
