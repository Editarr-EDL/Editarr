"""Naming is display metadata across source admission, workers, and provenance."""

import json
from pathlib import Path

from scanner import api, repository
from tests.jellyfin_item_support import jellyfin_target
from tests.scan_naming_support import (
    _mcf_import_options,
    published_bytes,
    upload_import,
)
from tests.scan_naming_support import (
    client_context as client_context,
)
from tests.scan_naming_support import (
    db as db,
)


def test_review_api_creates_empty_review_and_maps_stale_revision(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    video_path = tmp_path / "jellyfin-video.mkv"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 42.5)
    target = jellyfin_target(monkeypatch, db, video_path, name="Jellyfin Video")
    reviews = f"/api/library-items/{target['item_id']}/reviews"

    created = client.post(reviews, json={"name": "First"}, headers=headers)
    assert created.status_code == 201
    review = created.json()
    assert review["name"] == "First"
    assert review["memberships"] == []
    assert review["filters"] == []
    assert review["mcf_document"] == {
        "title": "Jellyfin Video",
        "year": None,
        "work_type": "movie",
        "season": None,
        "episode": None,
        "source_url": None,
        "imdb_url": None,
        "release": None,
        "comment": None,
        "material_start_seconds": 0.0,
        "material_end_seconds": None,
    }
    assert review["synchronization"] == {
        "start_seconds": 0.0,
        "end_seconds": 42.5,
        "end_mode": "file_end",
    }
    assert review["accepted_credits"] is None

    stale = client.patch(
        f"/api/reviews/{review['id']}",
        json={"revision": 99, "name": "Second"},
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_review_revision"

    listed = client.get(reviews, headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [review["id"]]


def test_review_retime_api_is_protected_structured_and_non_persistent(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    video_path = tmp_path / "retime.mkv"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 100.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 100.0)
    target = jellyfin_target(monkeypatch, db, video_path)
    created = client.post(
        f"/api/library-items/{target['item_id']}/reviews",
        json={"name": "Retime"},
        headers=headers,
    ).json()
    request = {
        "revision": created["revision"],
        "memberships": created["memberships"],
        "current_synchronization": created["synchronization"],
        "proposed_synchronization": {
            "start_seconds": 10,
            "end_seconds": 90,
            "end_mode": "before_credits",
        },
        "mcf_document": created["mcf_document"],
        "filters": [],
        "accepted_credits": created["accepted_credits"],
        "new_manual_filters": [],
    }

    assert client.post(f"/api/reviews/{created['id']}/retime", json=request).status_code == 401
    missing_csrf = {key: value for key, value in headers.items() if key != "X-CSRF-Token"}
    assert (
        client.post(
            f"/api/reviews/{created['id']}/retime", json=request, headers=missing_csrf
        ).status_code
        == 403
    )

    response = client.post(f"/api/reviews/{created['id']}/retime", json=request, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["synchronization"] == request["proposed_synchronization"]
    assert response.json()["mcf_document"]["material_start_seconds"] == 10
    assert response.json()["mcf_document"]["material_end_seconds"] == 90
    persisted = repository.get_saved_review(db, created["id"])
    assert persisted is not None
    assert persisted.revision == 0
    assert persisted.synchronization.start_seconds == 0
    assert persisted.synchronization.end_seconds == 100

    stale = client.post(
        f"/api/reviews/{created['id']}/retime",
        json=request | {"revision": 99},
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_review_revision"

    overflow = client.post(
        f"/api/reviews/{created['id']}/retime",
        json=request
        | {
            "proposed_synchronization": {
                "start_seconds": 0,
                "end_seconds": 101,
                "end_mode": "file_end",
            }
        },
        headers=headers,
    )
    assert overflow.status_code == 422
    assert overflow.json()["detail"]["code"] == "invalid_synchronization_bounds"
    assert overflow.json()["detail"]["document_fields"] == ["proposed_synchronization.end_seconds"]

    manual_id = "5ed5a613-56ab-489f-a5bf-f72d93d733a1"
    manual = client.post(
        f"/api/reviews/{created['id']}/retime",
        json=request
        | {
            "current_synchronization": {
                "start_seconds": 10,
                "end_seconds": 100,
                "end_mode": "file_end",
            },
            "proposed_synchronization": {
                "start_seconds": 0,
                "end_seconds": 100,
                "end_mode": "file_end",
            },
            "mcf_document": created["mcf_document"] | {"material_start_seconds": 10},
            "new_manual_filters": [
                {
                    "id": manual_id,
                    "source": "visual",
                    "category": "dispensable_scenes",
                    "subcategory": "dispensable",
                    "severity": "medium",
                    "mcf_channel": "video",
                    "mcf_comment": "End Credits",
                    "label": "Manual cue",
                    "details": None,
                    "group_key": None,
                    "start_seconds": 0,
                    "end_seconds": 20,
                    "confidence": 1,
                    "edit_action": "skip",
                    "selected": False,
                }
            ],
        },
        headers=headers,
    )
    assert manual.status_code == 200
    assert manual.json()["new_manual_filters"][0]["id"] == manual_id
    assert manual.json()["new_manual_filters"][0]["start_seconds"] == 0
    assert manual.json()["new_manual_filters"][0]["end_seconds"] == 20

    missing = client.post("/api/reviews/missing/retime", json=request, headers=headers)
    assert missing.status_code == 404
    assert missing.json() == {"code": "not_found", "detail": "Not found"}


def test_credits_routes_use_frozen_safe_validation_and_media_errors(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    malformed_target = client.post(
        "/api/credits-detections",
        json={"target": {"source": "manual", "video_path": None}},
        headers=headers,
    )
    assert malformed_target.status_code == 400
    assert malformed_target.json()["detail"]["code"] == "invalid_credits_target"
    assert "video_path" not in malformed_target.text

    invalid_target = client.post(
        "/api/credits-detections",
        json={
            "target": {"source": "jellyfin", "item_id": ""},
            "qwen_model": None,
        },
        headers=headers,
    )
    assert invalid_target.status_code == 400
    assert invalid_target.json()["detail"]["code"] == "invalid_credits_target"

    missing_media = client.post(
        "/api/credits-detections",
        json={
            "target": jellyfin_target(monkeypatch, db, tmp_path / "missing.mkv"),
            "qwen_model": None,
        },
        headers=headers,
    )
    assert missing_media.status_code == 503
    assert missing_media.json()["detail"]["code"] == "credits_media_unavailable"

    invalid_boundary = client.post(
        "/api/credits-detections/missing/accept",
        json={
            "context": {
                "kind": "import",
                "target": {"source": "jellyfin", "item_id": "movie"},
            },
            "accepted_boundary_seconds": -1,
        },
        headers=headers,
    )
    assert invalid_boundary.status_code == 422
    assert invalid_boundary.json()["detail"]["code"] == "invalid_credits_boundary"

    malformed_context = client.post(
        "/api/credits-detections/missing/accept",
        json={
            "context": {"kind": "unknown", "video_path": str(tmp_path / "secret.mkv")},
            "accepted_boundary_seconds": 1,
        },
        headers=headers,
    )
    assert malformed_context.status_code == 400
    assert malformed_context.json()["detail"]["code"] == "invalid_credits_target"
    assert str(tmp_path) not in malformed_context.text


def test_edl_import_creates_imported_scan_and_review_without_export(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 100.0)

    response = upload_import(
        client,
        "edl",
        {"name": "Imported review", "target": jellyfin_target(monkeypatch, db, media_path)},
        b"## profanity: hush\n1.000 2.000 1\n",
        filename="incoming.edl",
        headers=headers,
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["review"]["name"] == "Imported review"
    assert payload["scan"]["origin"] == "imported"
    assert payload["scan"]["import_source_name"] == "incoming.edl"
    assert payload["scan"]["created_at"] == payload["scan"]["imported_at"]
    assert payload["scan"]["updated_at"] == payload["scan"]["imported_at"]
    assert payload["review"]["created_at"] == payload["scan"]["imported_at"]
    assert payload["review"]["updated_at"] == payload["scan"]["imported_at"]
    assert payload["scan"]["finding_count"] == 1
    assert payload["warning"] is None
    assert db.query(repository.ScanQueueRow).count() == 0
    assert db.query(repository.MediaItemExportRow).count() == 0

    duplicate = {**payload["review"]["filters"][0], "id": "f440d3d6-e645-485b-9d5a-e02c2456d885"}
    invalid_save = client.put(
        f"/api/reviews/{payload['review']['id']}",
        json={
            "revision": payload["review"]["revision"],
            "name": payload["review"]["name"],
            "memberships": payload["review"]["memberships"],
            "filters": [payload["review"]["filters"][0], duplicate],
            "mcf_document": payload["review"]["mcf_document"],
            "synchronization": payload["review"]["synchronization"],
        },
        headers=headers,
    )
    assert invalid_save.status_code == 409
    assert invalid_save.json()["detail"]["code"] == "invalid_membership"
    assert db.query(repository.ReviewFilterRow).count() == 1


def test_edl_import_rejects_missing_authoritative_runtime(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    media_path = tmp_path / "unknown-duration.mkv"
    media_path.write_bytes(b"video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: None)

    response = upload_import(
        client,
        "edl",
        {"name": "Unknown runtime", "target": jellyfin_target(monkeypatch, db, media_path)},
        b"1 2 1\n",
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "media_runtime_unavailable"
    assert db.query(repository.ScanRow).count() == 0
    assert db.query(repository.SavedReviewRow).count() == 0


def test_mcf_imports_share_the_authoritative_aggregate(client_context, tmp_path, db, monkeypatch):
    client, headers = client_context
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    content = (Path(__file__).parent / "fixtures" / "mcf" / "valid-1.1.mcf").read_bytes()
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 6000.0)
    target = jellyfin_target(monkeypatch, db, media_path)

    first_response = upload_import(
        client,
        "mcf",
        _mcf_import_options("First import", target, start_seconds=4.02, end_seconds=5040.1),
        content,
        filename="incoming.mcf",
        headers=headers,
    )
    assert first_response.status_code == 201, first_response.text
    upload_response = client.post(
        "/api/mcf-imports/upload",
        files={"file": ("upload.mcf", content, "application/octet-stream")},
        data={
            "options": json.dumps(
                _mcf_import_options(
                    "Upload import",
                    target,
                    start_seconds=4.02,
                    end_seconds=5040.1,
                )
            )
        },
        headers=headers,
    )
    assert upload_response.status_code == 201, upload_response.text

    first_payload = first_response.json()
    upload_payload = upload_response.json()
    for payload, name, source_name in (
        (first_payload, "First import", "incoming.mcf"),
        (upload_payload, "Upload import", "upload.mcf"),
    ):
        assert payload["review"]["name"] == name
        assert payload["scan"]["import_source_name"] == source_name
        assert payload["scan"]["origin"] == "imported"
        assert payload["scan"]["finding_count"] == 3
        assert payload["source_version"] == "1.1.0"
        assert payload["review"]["memberships"][0]["role"] == "both"
        assert payload["scan"]["mcf_document"]["title"] == "Ozymandias"
        assert payload["review"]["mcf_document"]["title"] == "Ozymandias"
        assert payload["notices"][0]["fields"] == ["title", "work_type"]
    review_id = first_payload["review"]["id"]
    assert client.get(f"/api/reviews/{review_id}/mcf/preflight").status_code == 401
    preflight = client.get(f"/api/reviews/{review_id}/mcf/preflight", headers=headers)
    assert preflight.status_code == 200
    assert preflight.json() == {
        "review_id": review_id,
        "revision": 0,
        "eligible": True,
        "issues": [],
        "policy_owner_id": 1,
        "policy_revision": 0,
    }
    exported = client.post(
        f"/api/reviews/{review_id}/mcf",
        json={"revision": 0},
        headers=headers,
    )
    assert exported.status_code == 200, exported.text
    assert exported.json()["format"] == "mcf"
    assert exported.json()["exported_format_revision"] == 0
    assert (
        published_bytes(db, review_id, "mcf")
        == (Path(__file__).parent / "fixtures" / "mcf" / "exported-1.1.mcf").read_bytes()
    )
    edl_exported = client.post(
        f"/api/reviews/{review_id}/edl",
        json={"revision": 0},
        headers=headers,
    )
    assert edl_exported.status_code == 200, edl_exported.text
    assert published_bytes(db, review_id, "edl")
    missing_csrf = {key: value for key, value in headers.items() if key != "X-CSRF-Token"}
    assert (
        client.post(
            f"/api/reviews/{review_id}/mcf",
            json={"revision": 0},
            headers=missing_csrf,
        ).status_code
        == 403
    )
    stale = client.post(
        f"/api/reviews/{review_id}/mcf",
        json={"revision": 99},
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_review_revision"
    assert db.query(repository.ScanRow).count() == 2
    assert db.query(repository.SavedReviewRow).count() == 2
    assert db.query(repository.MediaItemExportRow).count() == 2
