"""Naming is display metadata across source admission, workers, and provenance."""

from unittest.mock import MagicMock

import pytest

from scanner import api, repository
from scanner.db import (
    MediaItemExportRow,
    MediaItemExportScanRow,
    MediaItemRow,
    ScanRow,
)
from scanner.models import (
    ExportFormat,
    JellyfinMediaItem,
    ReviewCreate,
    ReviewScanMembership,
)
from tests.jellyfin_item_support import jellyfin_target
from tests.scan_naming_support import (
    client_context as client_context,
)
from tests.scan_naming_support import (
    db as db,
)
from tests.scan_naming_support import (
    upload_import,
)


def test_scan_references_and_delete_conflict_are_structured(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 100.0)
    imported = upload_import(
        client,
        "edl",
        {"name": "Referenced review", "target": jellyfin_target(monkeypatch, db, media_path)},
        b"1 2 1\n",
        headers=headers,
    ).json()
    scan_id = imported["scan"]["id"]
    duplicate = client.post(
        f"/api/reviews/{imported['review']['id']}/duplicate",
        json={"revision": 0, "name": "Referenced review"},
        headers=headers,
    )
    assert duplicate.status_code == 201
    exported = client.post(
        f"/api/reviews/{imported['review']['id']}/edl",
        json={"revision": 0},
        headers=headers,
    )
    assert exported.status_code == 200
    references = client.get(f"/api/scans/{scan_id}/references", headers=headers)
    assert references.status_code == 200
    reference_payload = references.json()
    assert reference_payload["active_exports"] == ["edl"]
    assert {review["id"] for review in reference_payload["reviews"]} == {
        imported["review"]["id"],
        duplicate.json()["id"],
    }
    assert [review["name"] for review in reference_payload["reviews"]] == [
        "Referenced review",
        "Referenced review",
    ]
    assert all(review["updated_at"] for review in reference_payload["reviews"])
    conflict = client.delete(f"/api/scans/{scan_id}", headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "scan_referenced"
    assert conflict.json()["detail"]["references"] == reference_payload
    assert "Referenced review" in conflict.json()["detail"]["message"]
    assert client.get("/api/scans/missing/references", headers=headers).status_code == 404
    missing_delete = client.delete("/api/scans/missing", headers=headers)
    assert missing_delete.status_code == 404
    assert missing_delete.json()["detail"] == {
        "code": "scan_not_found",
        "message": "Scan not found",
        "line_number": None,
        "line_text": None,
        "references": None,
        "repair": None,
        "reason": None,
        "transient": None,
        "filter_ids": [],
        "document_fields": [],
        "scan_id": None,
        "candidate_id": None,
        "metadata_candidates": [],
        "import_issues": [],
    }


@pytest.mark.parametrize("recovered", [False, True])
def test_generic_export_status_and_delete_are_source_complete(
    client_context, tmp_path, db, monkeypatch, recovered
):
    client, headers = client_context
    media_path = tmp_path / "jellyfin.mkv"
    media_path.write_bytes(b"media")
    item_id = "item-1"
    item_type = "Movie"
    source_client = MagicMock()
    source_client.__enter__.return_value = source_client
    server_id = "http://jellyfin.test"
    status_path = f"/api/library-items/{item_id}/exports"
    delete_path = f"/api/library-items/{item_id}/exports/mcf"
    monkeypatch.setattr(api, "get_jellyfin_credentials", lambda _db: (server_id, "key"))
    source_client.item.return_value = JellyfinMediaItem(
        id=item_id, name="Movie", item_type=item_type, path=str(media_path)
    )
    monkeypatch.setattr(api, "JellyfinClient", lambda *_args: source_client)
    media = MediaItemRow(
        id="media-jellyfin",
        server_id=server_id,
        jellyfin_id=item_id,
        item_type=item_type,
        name="Movie",
        path=str(media_path),
    )
    scan = ScanRow(
        id="scan-jellyfin",
        media_item_id=media.id,
        scan_number=1,
        source_path=str(media_path),
        resolved_local_path=str(media_path),
        duration_seconds=100,
        status="complete",
        scan_audio=True,
        scan_video=False,
    )
    db.add_all([media, scan])
    db.commit()
    review = repository.create_saved_review(
        db,
        media_item_id=media.id,
        media_source_id=None,
        data=ReviewCreate(
            name="Review",
            memberships=[ReviewScanMembership(scan_id=scan.id, role="audio")],
        ),
    )
    db.add(
        MediaItemExportRow(
            media_item_id=media.id,
            media_source_id="",
            format=ExportFormat.MCF,
            review_id=review.id,
            exported_format_revision=review.mcf_content_revision,
            scans=[MediaItemExportScanRow(scan_id=scan.id, role="audio")],
        )
    )
    db.commit()
    media_path.with_suffix(".mcf").write_text("mcf")
    if recovered:
        from datetime import datetime, timezone

        from scanner.db import ReviewScanRepairRow
        from scanner.persistence.repository.review_repairs import recover_review

        export = db.query(MediaItemExportRow).one()
        export.scans.clear()
        db.add(
            ReviewScanRepairRow(
                review_id=review.id,
                missing_scan_id="missing-export-scan",
                detected_at=datetime.now(timezone.utc),
                preserved_json="{}",
            )
        )
        db.commit()
        assert recover_review(db, review.id).repair is None
        assert media_path.with_suffix(".mcf").read_text() == "mcf"

    assert client.get(status_path, params={"item_id": item_id}).status_code == 401
    missing_csrf = {key: value for key, value in headers.items() if key != "X-CSRF-Token"}
    assert (
        client.delete(delete_path, params={"item_id": item_id}, headers=missing_csrf).status_code
        == 403
    )
    status = client.get(status_path, params={"item_id": item_id}, headers=headers)
    assert status.status_code == 200, status.text
    assert status.json()["exports"]["edl"]["present"] is False
    assert status.json()["exports"]["mcf"]["current"] is not recovered

    deleted = client.delete(delete_path, params={"item_id": item_id}, headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert not media_path.with_suffix(".mcf").exists()
    assert db.query(MediaItemExportRow).count() == 0
    missing = client.delete(delete_path, params={"item_id": item_id}, headers=headers)
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "export_not_found"
