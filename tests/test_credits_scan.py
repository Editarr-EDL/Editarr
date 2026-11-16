import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository
from scanner.credits import credits_scan
from scanner.credits.credits_detector import (
    ChapterMarker,
    default_configuration,
    detect_from_observations,
)
from scanner.db import Base, ScanFindingRow
from scanner.models import ScanMode


def _credits_scan(db, video_path, *, qwen_model=None):
    media = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie",
        library_id="movies",
        item_type="Movie",
        name="Movie",
        path=str(video_path),
    )
    configuration = default_configuration(qwen_model=qwen_model)
    scan = repository.create_scan(
        db,
        media_item=media,
        resolved_local_path=str(video_path),
        duration_seconds=120.0,
        scan_audio=False,
        audio_model=None,
        audio_filter=None,
        scan_video=True,
        video_model=None,
        scan_mode=ScanMode.CREDITS_DETECTION,
        credits_detection_configuration=configuration,
    )
    return scan, configuration


def test_cached_credits_result_skips_media_analysis_and_completes(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    scan, configuration = _credits_scan(db, video_path)
    result = detect_from_observations(
        duration_seconds=120.0,
        configuration=configuration,
        chapters=[ChapterMarker(90.0, "End Credits")],
    )
    monkeypatch.setattr(credits_scan, "credits_cache_key", lambda **_kwargs: "a" * 64)
    monkeypatch.setattr(
        credits_scan, "load_cached_credits_result", lambda _key: result.model_dump(mode="json")
    )
    monkeypatch.setattr(
        credits_scan,
        "detect_credits_in_media",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("detector ran")),
    )
    progress = []
    stages = []

    returned = credits_scan.run_credits_scan(
        db,
        str(video_path),
        scan_id=scan.id,
        configuration=configuration,
        video_duration_seconds=120.0,
        video_hash=scan.video_hash,
        status_updater=repository.update_scan_status,
        progress_callback=progress.append,
        stage_callback=lambda stage, state, effective: stages.append((stage, state, effective)),
    )

    assert returned == result
    assert any(update["stage"] == "credits_cache" for update in progress)
    assert [(stage, state) for stage, state, _effective in stages] == [
        ("credits_cache", "started"), ("credits_cache", "complete"),
    ]
    assert all(effective["cache_hit"] for _stage, _state, effective in stages)
    assert db.query(ScanFindingRow).filter_by(scan_id=scan.id).count() == 1
    assert repository.get_scan(db, scan.id).status == "complete"
    db.close()


def test_credits_scan_rejects_media_replaced_after_queueing(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"original-physical-version")
    scan, configuration = _credits_scan(db, video_path)
    video_path.write_bytes(b"replacement-physical-version")
    monkeypatch.setattr(
        credits_scan,
        "detect_credits_in_media",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("detector ran")),
    )

    with pytest.raises(ValueError, match="media changed"):
        credits_scan.run_credits_scan(
            db,
            str(video_path),
            scan_id=scan.id,
            configuration=configuration,
            video_duration_seconds=120.0,
            video_hash=scan.video_hash,
            status_updater=repository.update_scan_status,
        )

    assert repository.get_scan(db, scan.id).status == "scanning"
    assert db.query(ScanFindingRow).filter_by(scan_id=scan.id).count() == 0
    db.close()


def test_uncached_credits_scan_runs_qwen_path_cleans_workspace_and_survives_cache_failure(
    monkeypatch, tmp_path
):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    scan, configuration = _credits_scan(
        db, video_path, qwen_model="qwen3-vl-4b-instruct-4bit"
    )
    result = detect_from_observations(
        duration_seconds=120.0,
        configuration=configuration,
        chapters=[ChapterMarker(90.0, "End Credits")],
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    removed = []
    assessed = []
    stages = []
    monkeypatch.setattr(credits_scan, "load_cached_credits_result", lambda _key: {"bad": True})
    monkeypatch.setattr(credits_scan, "create_scan_workspace", lambda *_args: workspace)
    monkeypatch.setattr(credits_scan, "remove_scan_workspace", removed.append)

    def assess(paths, timestamps, *, qwen_model):
        assessed.append((paths, timestamps, qwen_model))
        return "qwen-assessment"

    def detect(*_args, **kwargs):
        assert kwargs["workspace"] == workspace
        assert kwargs["qwen_assessor"](
            [workspace / "frame.jpg"], [90.0], "ignored-model"
        ) == "qwen-assessment"
        return result

    monkeypatch.setattr(credits_scan, "assess_frames_with_qwen", assess)
    monkeypatch.setattr(credits_scan, "detect_credits_in_media", detect)
    monkeypatch.setattr(
        credits_scan,
        "save_cached_credits_result",
        lambda *_args: (_ for _ in ()).throw(OSError("cache unavailable")),
    )

    returned = credits_scan.run_credits_scan(
        db,
        str(video_path),
        scan_id=scan.id,
        configuration=configuration,
        video_duration_seconds=120.0,
        video_hash=scan.video_hash,
        status_updater=repository.update_scan_status,
        stage_callback=lambda stage, state, effective: stages.append((stage, state, effective)),
    )

    assert returned == result
    assert assessed == [
        (
            [workspace / "frame.jpg"],
            [90.0],
            "qwen3-vl-4b-instruct-4bit",
        )
    ]
    assert removed == [workspace]
    assert [(stage, state) for stage, state, _effective in stages] == [
        ("credits_detection", "started"), ("credits_qwen", "started"),
        ("credits_qwen", "complete"), ("credits_detection", "complete"),
    ]
    assert repository.get_scan(db, scan.id).status == "complete"
    db.close()


def test_credits_scan_cancels_after_hash_before_cache_or_detector(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    scan, configuration = _credits_scan(db, video_path)
    cancellation_checks = iter((False, True))
    monkeypatch.setattr(
        credits_scan,
        "load_cached_credits_result",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cache read after cancellation")),
    )
    monkeypatch.setattr(
        credits_scan,
        "detect_credits_in_media",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("detector ran after cancellation")
        ),
    )

    with pytest.raises(credits_scan.ScanCancelledError):
        credits_scan.run_credits_scan(
            db,
            str(video_path),
            scan_id=scan.id,
            configuration=configuration,
            video_duration_seconds=120.0,
            video_hash=scan.video_hash,
            status_updater=repository.update_scan_status,
            should_cancel=lambda: next(cancellation_checks),
        )

    assert repository.get_scan(db, scan.id).status == "scanning"
    db.close()
