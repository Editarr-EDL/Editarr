"""Naming is display metadata across source admission, workers, and provenance."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from scanner import api, repository
from scanner.db import (
    MediaItemExportRow,
    MediaItemExportScanRow,
    ScanRow,
)
from scanner.models import (
    ExportFormat,
    FindingCreate,
    JellyfinMediaItem,
    JellyfinSettingsUpdate,
    McfCategoryFamily,
    ScanNameUpdate,
    ScanOrigin,
    normalize_review_name,
    normalize_scan_name,
)
from tests.scan_naming_support import (
    client_context as client_context,
)
from tests.scan_naming_support import (
    create_test_scan,
)
from tests.scan_naming_support import (
    db as db,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (" \u2003\u00a0", None),
        (" \u2003Éé Pass\u00a0", "Éé Pass"),
        ("😀" * 100, "😀" * 100),
    ],
)
def test_shared_name_canonicalizer(value, expected):
    assert normalize_scan_name(value) == expected
    assert ScanNameUpdate(name=value).name == expected
    if expected is not None:
        assert normalize_review_name(value) == expected
    elif value is not None:
        with pytest.raises(ValueError, match="required"):
            normalize_review_name(value)


@pytest.mark.parametrize(
    "value", ["x" * 101, "😀" * 101, "\nname", "name\t", "\x85", "a\0b", "\ud800"]
)
def test_name_validation_rejects_original_controls_surrogates_and_codepoint_overflow(db, value):
    with pytest.raises(ValueError):
        normalize_scan_name(value)
    with pytest.raises(ValidationError):
        ScanNameUpdate(name=value)
    with pytest.raises(ValueError):
        create_test_scan(db, name=value)
    assert db.query(ScanRow).count() == 0


@pytest.mark.parametrize(
    "status", ["queued", "scanning", "complete", "orphaned", "cancelling", "cancelled", "failed"]
)
@pytest.mark.parametrize("origin", list(ScanOrigin))
def test_rename_keeps_scan_identity_options_revisions_and_findings(
    db, client_context, status, origin
):
    client, headers = client_context
    scan = create_test_scan(db, origin=origin)
    row = db.get(ScanRow, scan.id)
    row.status = status
    row.worker_token = "worker-token"
    row.worker_pid = 123
    row.worker_pgid = 124
    row.worker_started_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    row.worker_heartbeat_at = row.worker_started_at
    row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    row.checkpoint_stage = "audio"
    row.selection_revision = 7
    if origin == ScanOrigin.IMPORTED:
        row.import_source_name = "import.edl"
        row.imported_at = datetime.now(timezone.utc)
    db.commit()
    repository.create_scan_findings_batch(
        db,
        scan.id,
        [
            FindingCreate(
                video_id=scan.id,
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=1,
                end_seconds=2,
                confidence=1,
                source="audio",
                label="word",
            )
        ],
    )
    queue = repository.enqueue_scan(db, scan.id) if origin == ScanOrigin.GENERATED else None
    db.refresh(row)
    before = {column.name: getattr(row, column.name) for column in ScanRow.__table__.columns}
    findings = repository.get_scan_findings(db, scan.id).model_dump()

    response = client.patch(
        f"/api/scans/{scan.id}", json={"name": "  Mixed Case 😀  "}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Mixed Case 😀"
    assert response.json()["origin"] == origin.value
    assert response.json()["manual"] == (origin == ScanOrigin.MANUAL)
    assert response.json()["finding_count"] == 1
    db.refresh(row)
    after = {column.name: getattr(row, column.name) for column in ScanRow.__table__.columns}
    for key in before.keys() - {"name"}:
        assert after[key] == before[key], key
    assert repository.get_scan_findings(db, scan.id).model_dump() == findings
    if queue:
        renamed_queue = repository.get_scan_queue_item_by_scan_id(db, scan.id)
        assert renamed_queue.queue_id == queue.queue_id
        assert renamed_queue.sort_order == queue.sort_order
        assert renamed_queue.name == "Mixed Case 😀"
    if origin == ScanOrigin.IMPORTED:
        assert response.json()["import_source_name"] == "import.edl"
        assert response.json()["imported_at"].endswith("Z")
        assert response.json()["scan_duration_seconds"] is None
    cleared = client.patch(f"/api/scans/{scan.id}", json={"name": None}, headers=headers)
    assert cleared.status_code == 200
    assert cleared.json()["name"] is None
    db.refresh(row)
    assert {
        column.name: getattr(row, column.name) for column in ScanRow.__table__.columns
    } == before


def test_rename_clears_duplicates_and_unknown_scan_is_safe(db, client_context):
    client, headers = client_context
    first = create_test_scan(db, name="Same")
    second = create_test_scan(db)
    boundary = client.patch(f"/api/scans/{second.id}", json={"name": "😀" * 100}, headers=headers)
    assert boundary.status_code == 200
    assert boundary.json()["name"] == "😀" * 100
    assert (
        client.patch(f"/api/scans/{second.id}", json={"name": "Same"}, headers=headers).status_code
        == 200
    )
    for value in [None, " \u2003 "]:
        response = client.patch(f"/api/scans/{first.id}", json={"name": value}, headers=headers)
        assert response.status_code == 200
        assert response.json()["name"] is None
    response = client.patch("/api/scans/missing", json={"name": "Name"}, headers=headers)
    assert response.status_code == 404
    assert response.json() == {
        "detail": {
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
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": 1},
        {"name": "x" * 101},
        {"name": "\nname"},
        {"name": "Valid", "video_path": "/private/secret.mkv"},
    ],
)
def test_rename_validation_redacts_inputs_and_does_not_mutate(db, client_context, payload):
    client, headers = client_context
    scan = create_test_scan(db, name="Before")
    response = client.patch(f"/api/scans/{scan.id}", json=payload, headers=headers)
    assert response.status_code == 422
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])
    assert "/private/secret.mkv" not in response.text
    assert db.get(ScanRow, scan.id).name == "Before"


def test_rename_requires_authentication_same_origin_and_csrf(db, client_context):
    client, headers = client_context
    scan = create_test_scan(db, name="Before")
    route = f"/api/scans/{scan.id}"
    assert client.patch(route, json={"name": "After"}).status_code == 401
    for bad_headers in [
        {key: value for key, value in headers.items() if key != "X-CSRF-Token"},
        {**headers, "Origin": "http://attacker.example"},
        {**headers, "X-CSRF-Token": "invalid"},
    ]:
        assert client.patch(route, json={"name": "After"}, headers=bad_headers).status_code == 403
        assert db.get(ScanRow, scan.id).name == "Before"
    assert client.patch(route, json={"name": "After"}, headers=headers).status_code == 200


@pytest.mark.parametrize("manual_finding", [False, True])
@pytest.mark.parametrize("initial_name", [None, " \u2003My Pass 😀 "])
def test_all_creation_routes_share_names_and_manual_reuse(
    db, client_context, monkeypatch, tmp_path, manual_finding, initial_name
):
    client, headers = client_context
    video = tmp_path / "example.mkv"
    video.touch()
    library_item = JellyfinMediaItem(
        id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path=str(video),
    )

    class FakeClient:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def item(self, _item_id):
            return library_item

    monkeypatch.setattr(api, "JellyfinClient", FakeClient)
    repository.update_jellyfin_settings(
        db, JellyfinSettingsUpdate(server_url="http://jellyfin.test", api_key="test")
    )
    route = "/api/library-items/movie-1/manual-findings" if manual_finding else "/api/scans"
    payload = (
        {"source": "audio", "start_seconds": 1, "end_seconds": 2, "word": "word"}
        if manual_finding
        else {"scan_audio": False, "scan_video": True}
    )
    if not manual_finding:
        payload["jellyfin_item_id"] = "movie-1"
    if manual_finding:
        assert client.post(route, json=payload, headers=headers).status_code == 404
        return
    for invalid in ["\tName", "😀" * 101]:
        response = client.post(route, json={**payload, "name": invalid}, headers=headers)
        assert response.status_code == 422, response.text
        assert all("input" not in error for error in response.json()["detail"])
        assert db.query(ScanRow).count() == 0
    named_payload = {**payload, "name": initial_name} if initial_name is not None else payload
    expected_name = normalize_scan_name(initial_name)
    response = client.post(route, json=named_payload, headers=headers)
    assert response.status_code == 201, response.text
    scan = response.json()["scan"] if manual_finding else response.json()
    assert scan["name"] == expected_name
    assert scan["origin"] == ("manual" if manual_finding else "generated")
    second_response = client.post(route, json=payload, headers=headers)
    assert second_response.status_code == 201, second_response.text
    second = second_response.json()["scan"] if manual_finding else second_response.json()
    if manual_finding:
        assert second["id"] == scan["id"]
        assert second["name"] == expected_name
        reused = client.post(route, json={**payload, "name": "Do not overwrite"}, headers=headers)
        assert reused.json()["scan"]["name"] == expected_name
    else:
        assert second["name"] is None


def test_names_flow_through_history_item_lists_candidates_and_edl_provenance(db):
    scan = create_test_scan(db, name="Evidence")
    row = db.get(ScanRow, scan.id)
    row.status = "complete"
    edl = MediaItemExportRow(media_item_id=scan.media_item_id, media_source_id="")
    db.add(edl)
    db.flush()
    db.add(MediaItemExportScanRow(media_item_edl_id=edl.id, scan_id=scan.id, role="audio"))
    db.commit()
    repository.rename_scan(db, scan.id, "Current name")
    lists = [
        repository.list_scan_history(db),
        repository.list_scans_for_library_item(
            db, server_id="http://jellyfin.test", jellyfin_item_id="movie-1"
        ),
        [
            match
            for match, _reason in repository.find_filter_candidates(
                db,
                resolved_local_path="/test-only/another-copy.mkv",
                source_path="/test-only/example.mkv",
                duration_seconds=120.0,
                scene_name=None,
                release_group=None,
                media_format=None,
                media_provider_id="provider-1",
            )
        ],
    ]
    for scans in lists:
        assert [(item.id, item.name, item.origin) for item in scans] == [
            (scan.id, "Current name", ScanOrigin.GENERATED)
        ]
    status = repository.media_item_export_status(
        db, server_id="http://jellyfin.test", jellyfin_item_id="movie-1"
    ).exports[ExportFormat.EDL]
    assert status.exported_scans[0].name == "Current name"
    assert status.exported_scans[0].origin == ScanOrigin.GENERATED


def test_openapi_freezes_naming_contract():
    schema = api.app.openapi()
    models = schema["components"]["schemas"]
    patch = schema["paths"]["/api/scans/{scan_id}"]["patch"]
    assert patch["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ScanNameUpdate"
    )
    assert patch["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/Scan"
    )
    assert patch["responses"]["404"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ReviewError"
    )
    assert models["ScanNameUpdate"]["required"] == ["name"]
    assert models["ScanNameUpdate"]["additionalProperties"] is False
    assert "name" in models["ScanRequest"]["properties"]
    assert "name" not in models["ScanRequest"].get("required", [])
    assert "ManualScanRequest" not in models
    assert "ManualScanFindingCreate" not in models
    assert "ManualFileScanFindingCreate" not in models
    for name in ["Scan", "ScanQueueItem", "ExportScanReference"]:
        assert {"name", "origin"} <= models[name]["properties"].keys()
    assert models["ScanOrigin"]["enum"] == ["generated", "manual", "imported"]
