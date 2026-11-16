
import pytest

from scanner import repository
from scanner.credits.credits_detector import (
    ChapterMarker,
    default_configuration,
    detect_from_observations,
)
from scanner.db import (
    CreditsDetectionRunRow,
    McfSeverityDefaultRow,
    ScanFindingRow,
)
from scanner.models import ScanMode, Severity
from tests.queue_support import _queue_factory


def test_create_credits_scan_persists_detection_and_queue_contract(monkeypatch, tmp_path):
    factory = _queue_factory()
    db = factory()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(repository, "compute_video_md5", lambda _path: "video-hash")
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie",
        library_id="movies",
        item_type="Movie",
        name="Movie",
        path=str(video_path),
    )
    configuration = default_configuration(qwen_model=None)

    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(video_path),
        scan_audio=False,
        audio_model=None,
        audio_filter=None,
        scan_video=True,
        video_model=None,
        scan_mode=ScanMode.CREDITS_DETECTION,
        credits_detection_configuration=configuration,
    )
    queue_item = repository.enqueue_scan(db, scan.id)

    run = db.get(CreditsDetectionRunRow, scan.id)
    assert scan.scan_mode == ScanMode.CREDITS_DETECTION
    assert scan.credits_detection is not None
    assert scan.credits_detection.configuration == configuration
    assert run is not None
    assert queue_item.scan_mode == ScanMode.CREDITS_DETECTION
    assert queue_item.credits_detection == scan.credits_detection


def test_complete_credits_detection_persists_canonical_finding(monkeypatch, tmp_path):
    factory = _queue_factory()
    db = factory()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(repository, "compute_video_md5", lambda _path: "video-hash")
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie",
        library_id="movies",
        item_type="Movie",
        name="Movie",
        path=str(video_path),
    )
    configuration = default_configuration(qwen_model=None)
    severity_default = (
        db.query(McfSeverityDefaultRow).filter_by(subcategory="dispensable").one()
    )
    severity_default.severity = Severity.HIGH
    db.commit()
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(video_path),
        scan_audio=False,
        audio_model=None,
        audio_filter=None,
        scan_video=True,
        video_model=None,
        scan_mode=ScanMode.CREDITS_DETECTION,
        credits_detection_configuration=configuration,
    )
    result = detect_from_observations(
        duration_seconds=120.0,
        configuration=configuration,
        chapters=[ChapterMarker(90.0, "End Credits")],
    )

    completed = repository.complete_credits_detection(
        db,
        scan_id=scan.id,
        cache_key="a" * 64,
        result=result,
    )

    finding = db.query(ScanFindingRow).filter_by(scan_id=scan.id).one()
    assert completed.status == "complete"
    assert completed.credits_detection is not None
    assert completed.credits_detection.result == result
    assert finding.start_seconds == 90.0
    assert finding.end_seconds == 120.0
    assert finding.category == "dispensable_scenes"
    assert finding.subcategory == "dispensable"
    assert finding.severity == Severity.HIGH
    assert finding.mcf_channel == "video"
    assert finding.mcf_comment == "End Credits"
    assert finding.label == "End Credits"
    assert finding.details == "End Credits"
    assert finding.source == "visual"
    assert finding.selected is True
    assert finding.edit_action == "skip"


def test_complete_credits_detection_with_no_candidate_creates_no_finding(
    monkeypatch, tmp_path
):
    factory = _queue_factory()
    db = factory()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(repository, "compute_video_md5", lambda _path: "video-hash")
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie",
        library_id="movies",
        item_type="Movie",
        name="Movie",
        path=str(video_path),
    )
    configuration = default_configuration(qwen_model=None)
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(video_path),
        scan_audio=False,
        audio_model=None,
        audio_filter=None,
        scan_video=True,
        video_model=None,
        scan_mode=ScanMode.CREDITS_DETECTION,
        credits_detection_configuration=configuration,
    )
    result = detect_from_observations(
        duration_seconds=120.0, configuration=configuration
    )

    completed = repository.complete_credits_detection(
        db, scan_id=scan.id, cache_key="a" * 64, result=result
    )

    assert completed.status == "complete"
    assert completed.finding_count == 0
    assert db.query(ScanFindingRow).filter_by(scan_id=scan.id).count() == 0


def test_complete_credits_detection_commit_failure_rolls_back_result_and_finding(
    monkeypatch, tmp_path
):
    factory = _queue_factory()
    db = factory()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(repository, "compute_video_md5", lambda _path: "video-hash")
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie",
        library_id="movies",
        item_type="Movie",
        name="Movie",
        path=str(video_path),
    )
    configuration = default_configuration(qwen_model=None)
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(video_path),
        scan_audio=False,
        audio_model=None,
        audio_filter=None,
        scan_video=True,
        video_model=None,
        scan_mode=ScanMode.CREDITS_DETECTION,
        credits_detection_configuration=configuration,
    )
    result = detect_from_observations(
        duration_seconds=120.0,
        configuration=configuration,
        chapters=[ChapterMarker(90.0, "End Credits")],
    )
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit")))

    with pytest.raises(RuntimeError, match="commit"):
        repository.complete_credits_detection(
            db, scan_id=scan.id, cache_key="a" * 64, result=result
        )

    db.expire_all()
    persisted = repository.get_scan(db, scan.id)
    assert persisted.status == "queued"
    assert persisted.credits_detection.result is None
    assert db.query(ScanFindingRow).filter_by(scan_id=scan.id).count() == 0


def test_interrupted_credits_model_acquisition_returns_to_consent(monkeypatch, tmp_path):
    factory = _queue_factory()
    db = factory()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(repository, "compute_video_md5", lambda _path: "video-hash")
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie",
        library_id="movies",
        item_type="Movie",
        name="Movie",
        path=str(video_path),
    )
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(video_path),
        scan_audio=False,
        audio_model=None,
        audio_filter=None,
        scan_video=True,
        video_model=None,
        scan_mode=ScanMode.CREDITS_DETECTION,
        credits_detection_configuration=default_configuration(
            qwen_model="qwen3-vl-4b-instruct-4bit"
        ),
        initial_status="acquiring_model",
    )
    repository.enqueue_scan(db, scan.id)

    assert repository.recover_interrupted_credits_model_acquisitions(db) == 1

    recovered = repository.get_scan_queue_item_by_scan_id(db, scan.id)
    assert recovered.status == "awaiting_model_consent"
    assert recovered.progress_message == (
        "Model acquisition was interrupted; confirm to retry"
    )


def test_terminal_credits_scan_queue_row_is_recovered(monkeypatch, tmp_path):
    factory = _queue_factory()
    db = factory()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(repository, "compute_video_md5", lambda _path: "video-hash")
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie",
        library_id="movies",
        item_type="Movie",
        name="Movie",
        path=str(video_path),
    )
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(video_path),
        scan_audio=False,
        audio_model=None,
        audio_filter=None,
        scan_video=True,
        video_model=None,
        scan_mode=ScanMode.CREDITS_DETECTION,
        credits_detection_configuration=default_configuration(qwen_model=None),
    )
    repository.enqueue_scan(db, scan.id)
    repository.update_scan_status(db, scan.id, "complete")

    assert repository.recover_terminal_scan_queue_items(db) == 1
    assert repository.get_scan_queue_item_by_scan_id(db, scan.id) is None
    assert repository.get_scan(db, scan.id).status == "complete"


