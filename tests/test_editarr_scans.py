from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from scanner import api, repository
from scanner.db import (
    ScanFindingRow,
    ScanRow,
)
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.models import (
    EditAction,
    FindingCreate,
    JellyfinMediaVersion,
    JellyfinSettingsUpdate,
    McfCategoryFamily,
    NewManualReviewFilter,
    ReviewCreate,
    ReviewFilterInput,
    ReviewSave,
    ReviewScanMembership,
)
from scanner.persistence.repository.media_identity_store import (
    get_version,
    scan_version_id,
    version_locators,
)
from scanner.pipeline.audio_tracks import AudioTrack, AudioTrackInventory
from scanner.runtime.scan_version import prepare_scan_fingerprint
from scanner.security.api_key_crypto import load_api_key_wrap_key
from scanner.security.api_keys import rotate_api_key
from scanner.security.auth_repository import owner_id
from tests.editarr_support import _isolate_api_runtime
from tests.editarr_support import db as db
from tests.jellyfin_item_support import jellyfin_target
from tests.multi_user_access_support import add_linked_owner
from tests.scan_access_support import owner_caller


@pytest.fixture
def audio_selection(monkeypatch):
    from scanner.http import audio_tracks_api
    from scanner.persistence.repository import repository_scan_lifecycle

    inventory = AudioTrackInventory(
        "a" * 64, (AudioTrack(1, "eng", "Main", "aac", 2, 1, 0.0, 123.5, "1/48000"),)
    )
    monkeypatch.setattr(audio_tracks_api, "probe_audio_tracks", lambda _path: inventory)
    monkeypatch.setattr(repository_scan_lifecycle, "probe_audio_tracks", lambda _path: inventory)
    return AudioSelection.from_selection(inventory.select())


def test_scans_are_queued_from_jellyfin_items_with_per_item_scan_numbers(
    monkeypatch, db, tmp_path: Path, audio_selection
):
    video = tmp_path / "example.mkv"
    video.write_bytes(b"video")
    alternate_video = tmp_path / "alternate.mkv"
    alternate_video.write_bytes(b"alternate video")
    repository.update_jellyfin_settings(
        db,
        JellyfinSettingsUpdate(server_url="http://jellyfin.test", api_key="saved-server-key"),
    )

    class FakeJellyfinClient:
        def __init__(self, *_credentials):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def item(self, item_id):
            return api.JellyfinMediaItem(
                id=item_id,
                library_id="movies",
                item_type="Movie",
                name="Example",
                path=str(video),
                duration_seconds=123.5,
                versions=[
                    JellyfinMediaVersion(
                        id="source-2", name="Alternate cut", path=str(alternate_video)
                    )
                ],
            )

    monkeypatch.setattr(api, "JellyfinClient", FakeJellyfinClient)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    request = api.ScanRequest(
        audio_selection=audio_selection,
        jellyfin_item_id="movie-1",
        coarse_scan_enabled=True,
        coarse_scan_interval=2.0,
        qwen_model="qwen3-vl-4b-instruct-4bit",
        qwen_summaries_per_segment=3,
        qwen_segment_join_gap_seconds=1.5,
    )

    first = api.api_enqueue_scan(request, owner_caller(db))
    second = api.api_enqueue_scan(
        api.ScanRequest(
            audio_selection=audio_selection,
            jellyfin_item_id="movie-1",
            media_source_id="source-2",
            coarse_scan_enabled=True,
            coarse_scan_interval=2.0,
        ),
        owner_caller(db),
    )

    assert first.scan_id == 1
    assert second.scan_id == 2
    assert first.item_name == "Example"
    first_row = db.get(ScanRow, first.id)
    assert first_row is not None
    assert first_row.source_path == str(video)
    assert first_row.duration_seconds == 123.5
    assert first_row.speed_up is True
    assert first_row.frame_interval == 2.0
    assert first_row.qwen_model == "qwen3-vl-4b-instruct-4bit"
    assert first_row.qwen_summaries_per_segment == 3
    assert first_row.qwen_segment_join_gap_seconds == 1.5
    second_row = db.get(ScanRow, second.id)
    assert second_row is not None
    assert second_row.source_path == str(alternate_video)
    assert second_row.media_source_id == "source-2"
    assert second_row.media_source_name == "Alternate cut"
    assert second.media_source_name == "Alternate cut"
    queued = api.api_list_scan_queue(owner_caller(db))
    assert [item.id for item in queued] == [first.id, second.id]
    assert all(item.coarse_scan_enabled and item.coarse_scan_interval == 2.0 for item in queued)
    for item, path in ((first, video), (second, alternate_video)):
        assert scan_version_id(db, item.id) is None
        prepare_scan_fingerprint(
            db,
            item.id,
            str(path),
            write=lambda operation, *args: operation(db, *args),
        )
    first_version = scan_version_id(db, first.id)
    second_version = scan_version_id(db, second.id)
    assert first_version is not None and second_version is not None
    assert first_version != second_version
    assert get_version(db, first_version).title_id == get_version(db, second_version).title_id
    assert version_locators(db, second_version)[0].media_source_id == "source-2"
    repository.update_scan_duration(db, first.id, 123.5)
    repository.update_scan_progress(
        db,
        first.id,
        {"percent": 42.5, "stage": "visual", "message": "Scanning frames"},
    )
    assert [item.scan_id for item in api.api_list_scan_queue(owner_caller(db))] == [1, 2]
    persisted_queue_item = api.api_list_scan_queue(owner_caller(db))[0]
    assert persisted_queue_item.duration_seconds == 123.5
    assert persisted_queue_item.progress_percent == 42.5
    assert persisted_queue_item.progress_stage == "visual"
    assert persisted_queue_item.qwen_model == "qwen3-vl-4b-instruct-4bit"
    repository.update_scan_status(db, first.id, "scanning")
    next_item = repository.get_next_scan_queue(db)
    assert next_item is not None
    assert next_item.id == second.id
    repository.update_scan_status(db, first.id, "queued")
    repository.reorder_scan_queue(db, [second.queue_id, first.queue_id])
    assert [item.scan_id for item in api.api_list_scan_queue(owner_caller(db))] == [2, 1]
    assert repository.cancel_scan_queue_item(db, second.queue_id) == (second.id, False)
    assert [item.scan_id for item in api.api_list_scan_queue(owner_caller(db))] == [1]
    assert [item.status for item in api.api_list_scan_history(owner_caller(db))] == ["cancelled"]
    assert [
        scan.scan_id
        for scan in api.api_list_library_item_scans("movie-1", db, caller=owner_caller(db))
    ] == [2, 1]
    finished = db.get(ScanRow, first.id)
    finished.status = "complete"
    db.commit()
    assert repository.cancel_scan_queue_item(db, first.queue_id) == (first.id, False)
    db.expire_all()
    assert db.get(ScanRow, first.id).status == "complete"
    assert api.api_list_scan_queue(owner_caller(db)) == []
    # A worker re-marking its own error after cancellation settled it keeps it terminal.
    repository.update_scan_status(db, first.id, "error", "decoder failed")
    repository.mark_scan_orphaned(db, first.id, "Orphaned scan — worker stopped; ready to resume")
    db.expire_all()
    assert db.get(ScanRow, first.id).status == "error"


@pytest.mark.parametrize("audio_filter", ["better_profanity", "qwen"])
def test_audio_scan_persists_selected_filter(
    db, tmp_path, monkeypatch, audio_selection, audio_filter
):
    video = tmp_path / "selected-filter.mkv"
    video.write_bytes(b"video")
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    monkeypatch.setattr(api, "require_available", lambda _backend: None)
    target = jellyfin_target(monkeypatch, db, video, duration_seconds=60.0)
    request = api.ScanRequest(
        jellyfin_item_id=target["item_id"],
        scan_video=False,
        audio_selection=audio_selection,
        audio_filter=audio_filter,
        qwen_model="qwen3-vl-4b-instruct-4bit",
    )

    queued = api.api_enqueue_scan(request, owner_caller(db))
    row = db.get(ScanRow, queued.id)

    assert queued.audio_filter == row.audio_filter == audio_filter
    assert queued.qwen_model == row.qwen_model == "qwen3-vl-4b-instruct-4bit"
    assert api.ScanOptions(scan_video=False).audio_filter == "better_profanity"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "PUT",
            "/api/scans/{scan_id}/findings/selection",
            {"revision": 0, "selected_finding_ids": []},
        ),
        (
            "PUT",
            "/api/scans/{scan_id}/findings/1",
            {
                "revision": 0,
                "start_seconds": 1,
                "end_seconds": 2,
                "word": "edited",
            },
        ),
        ("DELETE", "/api/scans/{scan_id}/findings/1?revision=0", None),
        ("GET", "/api/scans/{scan_id}/findings/1/mute-preview", None),
        ("GET", "/api/scans/{scan_id}/findings/1/video-preview", None),
        ("POST", "/api/scans/{scan_id}/edl", {"audio_scan_ids": [], "video_scan_ids": []}),
    ],
)
def test_legacy_scan_authority_routes_are_not_available(
    monkeypatch, db, tmp_path, method, path, payload
):
    """Saved reviews are the only mutable selection and export authority."""
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-selection-route",
        library_id="movies",
        item_type="Movie",
        name="Selection route",
        path="/remote/selection-route.mkv",
    )
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path="/scanner/selection-route.mkv",
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_model=None,
        frame_interval=None,
        speed_up=False,
    )
    repository.create_scan_findings_batch(
        db,
        scan.id,
        [
            FindingCreate(
                video_id="unused",
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=10,
                end_seconds=11,
                confidence=0.9,
                source="audio",
            )
        ],
    )

    def override_db():
        yield db

    monkeypatch.setattr(api, "init_db", lambda: None)
    _isolate_api_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    monkeypatch.setattr(api, "recover_cancelled_scan_queue_items", lambda _db: 0)
    monkeypatch.setattr(api, "scanning_scan_worker_processes", lambda _db: [])
    monkeypatch.setattr(api, "mark_orphaned_scan_workers", lambda _db, _pids: [])
    monkeypatch.setattr(api, "get_session_factory", lambda: sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(
        "scanner.security.auth_policy.get_session_factory", lambda: sessionmaker(bind=db.get_bind())
    )
    add_linked_owner(db, "Admin")
    api_key = rotate_api_key(db, owner_id(db), load_api_key_wrap_key(tmp_path))
    api.app.dependency_overrides[api.get_db] = override_db
    try:
        with TestClient(api.app, headers={"X-Api-Key": api_key}) as client:
            response = client.request(
                method,
                path.format(scan_id=scan.id),
                **({"json": payload} if payload is not None else {}),
            )
    finally:
        api.app.dependency_overrides.clear()

    assert response.status_code == 404
    findings = repository.get_scan_findings(db, scan.id)
    assert findings is not None
    assert findings.revision == 0
    assert findings.findings[0].selected is True


def test_review_manual_filters_share_one_immutable_evidence_scan(db, tmp_path: Path):
    source = tmp_path / "manual-example.mkv"
    source.touch()
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path=str(source),
    )

    audio_review = repository.create_saved_review(
        db,
        media_item_id=media_item.id,
        media_source_id=None,
        duration_seconds=100,
        data=ReviewCreate(
            name="Audio review",
            new_manual_filters=[
                NewManualReviewFilter(
                    id="d6fef60e-2703-49b9-833f-553eda7d38fa",
                    source="audio",
                    category=McfCategoryFamily.LANGUAGE,
                    subcategory="swearing",
                    label="damn",
                    details="Dialogue",
                    group_key="damn",
                    start_seconds=5,
                    end_seconds=6,
                    edit_action=EditAction.MUTE,
                )
            ],
        ),
    )
    video_review = repository.create_saved_review(
        db,
        media_item_id=media_item.id,
        media_source_id=None,
        duration_seconds=100,
        data=ReviewCreate(
            name="Video review",
            new_manual_filters=[
                NewManualReviewFilter(
                    id="91bf6237-b1be-45ca-9462-e2a74f80bc06",
                    source="visual",
                    category=McfCategoryFamily.DRUGS,
                    subcategory="drugs",
                    label="drug use",
                    details="Fight scene",
                    group_key="drug use",
                    start_seconds=20,
                    end_seconds=30,
                    edit_action=EditAction.SKIP,
                )
            ],
        ),
    )

    assert audio_review.memberships[0].scan_id == video_review.memberships[0].scan_id
    manual_scan = db.get(ScanRow, audio_review.memberships[0].scan_id)
    assert manual_scan is not None
    assert manual_scan.origin == "manual"
    assert manual_scan.status == "complete"
    assert manual_scan.scan_audio is True
    assert manual_scan.scan_video is True
    findings = repository.get_scan_findings(db, manual_scan.id)
    assert findings is not None
    assert [finding.source for finding in findings.findings] == ["audio", "visual"]
    assert findings.findings[1].category == McfCategoryFamily.DRUGS
    assert findings.findings[1].subcategory == "drugs"
    assert findings.findings[1].label == "drug use"
    assert findings.findings[1].group_key == "drug use"
    # Each review owns only the Manual filter explicitly staged for it.
    assert [value.label for value in audio_review.filters] == ["damn"]
    assert [value.label for value in video_review.filters] == ["drug use"]

    reused_review = repository.create_saved_review(
        db,
        media_item_id=media_item.id,
        media_source_id=None,
        duration_seconds=100,
        data=ReviewCreate(
            name="Reused audio evidence",
            memberships=[ReviewScanMembership(scan_id=manual_scan.id, role="audio")],
        ),
    )
    assert reused_review.filters[0].source_finding_id == audio_review.filters[0].source_finding_id
    assert reused_review.filters[0].id != audio_review.filters[0].id

    edited_reuse = reused_review.filters[0].model_copy(update={"label": "review-local edit"})
    repository.save_saved_review(
        db,
        reused_review.id,
        ReviewSave(
            revision=reused_review.revision,
            name=reused_review.name,
            memberships=reused_review.memberships,
            filters=[ReviewFilterInput(**edited_reuse.model_dump())],
            mcf_document=reused_review.mcf_document,
            synchronization=reused_review.synchronization,
        ),
    )
    assert repository.get_saved_review(db, audio_review.id).filters[0].label == "damn"
    assert db.get(ScanFindingRow, audio_review.filters[0].source_finding_id).label == "damn"
