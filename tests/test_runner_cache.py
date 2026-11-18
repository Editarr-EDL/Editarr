from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from scanner.model_acquisition import get_acquisition_service
from scanner.models import FindingCreate, McfCategoryFamily
from scanner.pipeline import runner
from scanner.pipeline.runner_workers import _collect_isolated_findings
from scanner.settings.model_scan_settings import ModelScanSettings, save_model_scan_settings


def test_audio_worker_relays_transcription_progress(monkeypatch):
    events: list[dict[str, object]] = []
    models: list[str | None] = []

    def fake_scan_audio(*_args, progress_callback, **kwargs):
        models.append(kwargs.get("qwen_model"))
        assert kwargs["audio_padding_seconds"] == 0.45
        progress_callback(0.5, "Transcribed audio chunk 1/2")
        return []

    monkeypatch.setattr(runner, "scan_audio", fake_scan_audio)
    runner._scan_audio_worker(
        "audio.wav",
        "scan-1",
        "mlx_whisper",
        "better_profanity",
        None,
        "transcript.json",
        SimpleNamespace(put=events.append),
        Event(),
        "qwen3-vl-4b-instruct-4bit",
        0.45,
    )

    assert models == ["qwen3-vl-4b-instruct-4bit"]
    assert events == [
        {"progress": (0.5, "Transcribed audio chunk 1/2")},
        {"findings": []},
    ]


def test_audio_worker_cancellation_stops_lazy_acquisition_before_inference(monkeypatch, tmp_path):
    events: list[dict[str, object]] = []
    cancelled = Event()
    cancelled.set()
    inferred = False

    def fake_scan_audio(*_args, **_kwargs):
        nonlocal inferred
        get_acquisition_service(tmp_path / "models").acquire("faster-whisper-base")
        inferred = True
        return []

    monkeypatch.setattr(runner, "scan_audio", fake_scan_audio)
    runner._scan_audio_worker(
        "audio.wav",
        "scan-1",
        "whisper",
        "better_profanity",
        None,
        None,
        SimpleNamespace(put=events.append),
        cancelled,
    )

    assert events == [{"cancelled": True}]
    assert inferred is False


def test_isolated_audio_scan_settles_a_cooperative_acquisition_cancellation(monkeypatch):
    cancelled = Event()

    class FakeQueue:
        def get(self, *, timeout):
            assert timeout == 0.2
            return {"cancelled": True}

    class FakeProcess:
        def start(self):
            pass

        def join(self, *, timeout):
            assert timeout == 2

        def is_alive(self):
            return False

    class FakeContext:
        def Queue(self):
            return FakeQueue()

        def Event(self):
            return cancelled

        def Process(self, *, target, args):
            assert target is runner._scan_audio_worker
            assert args[-1] == 0.45
            return FakeProcess()

    monkeypatch.setattr(runner.mp, "get_context", lambda _name: FakeContext())

    with pytest.raises(runner.ScanCancelledError, match="Scan cancelled"):
        runner._scan_audio_isolated(
            "audio.wav",
            "scan-1",
            should_cancel=lambda: True,
            audio_padding_seconds=0.45,
        )

    assert cancelled.is_set()


def test_isolated_worker_reports_child_cancellation():
    process = SimpleNamespace(join=lambda timeout: None, is_alive=lambda: False)
    queue = SimpleNamespace(get=lambda timeout: {"cancelled": True})

    with pytest.raises(runner.ScanCancelledError, match="Scan cancelled"):
        _collect_isolated_findings(process, queue, Event(), None, None, kind="audio")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"scan_audio": False, "scan_video": False}, "At least one scan type"),
        (
            {"scan_audio": True, "scan_video": False, "coarse_scan_enabled": True},
            "Coarse scanning requires visual scanning",
        ),
        (
            {
                "scan_audio": False,
                "scan_video": True,
                "coarse_scan_enabled": True,
                "coarse_scan_interval_seconds": 0,
            },
            "Coarse scan interval must be greater than zero",
        ),
        (
            {"scan_audio": False, "scan_video": True, "qwen_summaries_per_segment": 0},
            "Representative frames per flagged segment",
        ),
        (
            {"scan_audio": False, "scan_video": True, "qwen_segment_join_gap_seconds": -1},
            "Qwen segment join gap cannot be negative",
        ),
        (
            {"scan_audio": False, "scan_video": True, "qwen_model": "unknown-qwen"},
            "Unsupported Qwen model",
        ),
    ],
)
def test_invalid_scan_options_allocate_no_workspace(monkeypatch, tmp_path: Path, kwargs, message):
    video_path = tmp_path / "sample.mkv"
    video_path.write_bytes(b"video-bytes")
    frames_dir = tmp_path / "frames"
    monkeypatch.setattr(runner.settings, "frames_dir", frames_dir)
    monkeypatch.setattr(
        runner,
        "get_video_duration",
        lambda _video: (_ for _ in ()).throw(AssertionError("invalid options must fail first")),
    )

    with pytest.raises(ValueError, match=message):
        runner.run_scan(
            db=object(),
            video_path=str(video_path),
            video_id="scan-1",
            status_updater=lambda *_args: None,
            findings_writer=lambda *_args: None,
            **kwargs,
        )

    assert not frames_dir.exists()


def test_invalid_scene_threshold_allocates_no_workspace(monkeypatch, tmp_path: Path):
    video_path = tmp_path / "sample.mkv"
    video_path.write_bytes(b"video-bytes")
    frames_dir = tmp_path / "frames"
    monkeypatch.setattr(runner.settings, "frames_dir", frames_dir)
    monkeypatch.setattr(runner.settings, "scene_detection_threshold", 0)
    monkeypatch.setattr(
        runner,
        "get_video_duration",
        lambda _video: (_ for _ in ()).throw(AssertionError("invalid options must fail first")),
    )

    with pytest.raises(ValueError, match="Scene detection threshold must be greater than zero"):
        runner.run_scan(
            db=object(),
            video_path=str(video_path),
            scan_audio=False,
            scan_video=True,
            video_id="scan-1",
            status_updater=lambda *_args: None,
            findings_writer=lambda *_args: None,
        )

    assert not frames_dir.exists()


def test_run_scan_reuses_cached_audio_and_model_scoped_transcript(monkeypatch, tmp_path: Path):
    video_path = tmp_path / "sample.mkv"
    video_path.write_bytes(b"video-bytes")

    cache_audio = tmp_path / "cache" / "videos" / "hash123" / "audio.wav"
    cache_audio.parent.mkdir(parents=True, exist_ok=True)
    cache_audio.write_bytes(b"wav-data")

    seen: dict[str, str] = {}

    def fake_scan_audio(audio_path, _video_id, **kwargs):
        seen["audio_path"] = audio_path
        seen["transcript_cache_path"] = kwargs["transcript_cache_path_value"]
        seen["qwen_model"] = kwargs["qwen_model"]
        return []

    monkeypatch.setattr(runner.settings, "frames_dir", tmp_path / "frames")
    monkeypatch.setattr(runner, "get_video_duration", lambda _video: 120.0)
    monkeypatch.setattr(runner, "compute_video_md5", lambda _video: "hash123")
    monkeypatch.setattr(runner, "cached_audio_path", lambda _hash: cache_audio)
    monkeypatch.setattr(
        runner,
        "transcription_cache_path",
        lambda video_hash, model_name: tmp_path
        / "cache"
        / "videos"
        / video_hash
        / f"transcript_{model_name}.json",
    )
    monkeypatch.setattr(runner, "extract_audio", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("extract_audio should be skipped when cached audio exists")
    ))
    monkeypatch.setattr(runner, "_scan_audio_isolated", fake_scan_audio)

    statuses: list[str] = []
    stages = []
    runner.run_scan(
        db=object(),
        video_path=str(video_path),
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        qwen_model="qwen3-vl-4b-instruct-4bit",
        scan_video=False,
        video_id="scan-1",
        status_updater=lambda _db, _video_id, status: statuses.append(status),
        findings_writer=lambda _db, _video_id, _findings: None,
        stage_callback=lambda stage, state, effective: stages.append((stage, state, effective)),
    )

    assert seen["audio_path"] == str(cache_audio)
    assert seen["transcript_cache_path"].endswith("transcript_whisper_base.json")
    assert seen["qwen_model"] == "qwen3-vl-4b-instruct-4bit"
    assert statuses == ["scanning", "complete"]
    assert ("audio_extract", "reused") in [
        (stage, state) for stage, state, _effective in stages
    ]


def test_run_scan_isolates_scene_based_visual_scan_for_qwen_summaries(monkeypatch, tmp_path: Path):
    video_path = tmp_path / "sample.mkv"
    video_path.write_bytes(b"video-bytes")

    seen: dict[str, object] = {}

    def fake_visual_isolated(_video_path, _video_id, _work_dir, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(runner.settings, "frames_dir", tmp_path / "frames")
    monkeypatch.setattr(runner, "get_video_duration", lambda _video: 120.0)
    monkeypatch.setattr(runner, "compute_video_md5", lambda _video: "hash123")
    monkeypatch.setattr(runner, "_scan_visual_isolated", fake_visual_isolated)

    statuses: list[str] = []
    runner.run_scan(
        db=object(),
        video_path=str(video_path),
        scan_audio=False,
        scan_video=True,
        coarse_scan_enabled=True,
        coarse_scan_interval_seconds=6,
        video_id="scan-1",
        status_updater=lambda _db, _video_id, status: statuses.append(status),
        findings_writer=lambda _db, _video_id, _findings: None,
    )

    assert seen["coarse_scan_enabled"] is True
    assert seen["coarse_scan_interval_seconds"] == 6
    assert seen["qwen_summaries_per_segment"] == 4
    assert seen["qwen_segment_join_gap_seconds"] == 3.0
    assert seen["video_hash"] == "hash123"
    assert statuses == ["scanning", "complete"]


def test_run_scan_isolates_image_safety_gate_before_audio(monkeypatch, tmp_path: Path):
    video_path = tmp_path / "sample.mkv"
    video_path.write_bytes(b"video-bytes")

    seen: dict[str, object] = {}

    def fake_visual_isolated(_video_path, _video_id, _work_dir, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(runner.settings, "frames_dir", tmp_path / "frames")
    monkeypatch.setattr(runner, "get_video_duration", lambda _video: 120.0)
    monkeypatch.setattr(runner, "compute_video_md5", lambda _video: "hash123")
    monkeypatch.setattr(runner, "_scan_visual_isolated", fake_visual_isolated)
    monkeypatch.setattr(
        runner,
        "_scan_visual_by_scene",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Image Safety Gate must run in an isolated process")
        ),
    )

    runner.run_scan(
        db=object(),
        video_path=str(video_path),
        scan_audio=False,
        scan_video=True,
        video_model="image_safety_gate",
        coarse_scan_enabled=True,
        coarse_scan_interval_seconds=6,
        video_id="scan-1",
        status_updater=lambda _db, _video_id, _status: None,
        findings_writer=lambda _db, _video_id, _findings: None,
    )

    assert seen["coarse_scan_enabled"] is True
    assert seen["video_hash"] == "hash123"
    assert seen["video_model"] == "image_safety_gate"


def test_run_scan_does_not_apply_review_time_defaults_to_audio_findings(
    monkeypatch, tmp_path: Path
):
    video_path = tmp_path / "sample.mkv"
    video_path.write_bytes(b"video-bytes")

    captured: dict[str, object] = {}
    stage_values = {}
    monkeypatch.setattr(runner.settings, "data_dir", tmp_path)
    save_model_scan_settings(tmp_path, ModelScanSettings(audio_padding_seconds=0.45))

    def fake_scan_audio_isolated(_audio_path, _video_id, **kwargs):
        captured["deny_words"] = kwargs.get("deny_words")
        captured["audio_padding_seconds"] = kwargs["audio_padding_seconds"]
        save_model_scan_settings(tmp_path, ModelScanSettings(audio_padding_seconds=0.7))
        return [
            FindingCreate(
                video_id="scan-1",
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=1.0,
                end_seconds=2.0,
                confidence=0.9,
                label="dang",
                source="audio",
                selected=True,
            )
        ]

    saved_findings: list[FindingCreate] = []

    monkeypatch.setattr(runner.settings, "frames_dir", tmp_path / "frames")
    monkeypatch.setattr(runner, "get_video_duration", lambda _video: 120.0)
    monkeypatch.setattr(runner, "compute_video_md5", lambda _video: "hash123")
    monkeypatch.setattr(
        runner,
        "cached_audio_path",
        lambda _hash: tmp_path / "cache" / "videos" / "hash123" / "audio.wav",
    )
    monkeypatch.setattr(
        runner,
        "extract_audio",
        lambda *_args, **_kwargs: str(tmp_path / "cache" / "videos" / "hash123" / "audio.wav"),
    )
    monkeypatch.setattr(
        runner,
        "transcription_cache_path",
        lambda _video_hash, _model_name: tmp_path
        / "cache"
        / "videos"
        / "hash123"
        / "transcript_base.json",
    )
    monkeypatch.setattr(runner, "_scan_audio_isolated", fake_scan_audio_isolated)
    runner.run_scan(
        db=object(),
        video_path=str(video_path),
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_id="scan-1",
        status_updater=lambda _db, _video_id, _status: None,
        findings_writer=lambda _db, _video_id, findings: saved_findings.extend(findings),
        stage_callback=lambda stage, _state, values: stage_values.update({stage: values}),
    )

    assert captured["deny_words"] is None
    assert captured["audio_padding_seconds"] == 0.45
    assert stage_values["audio_scan"]["audio_padding_seconds"] == 0.45
    assert stage_values["audio_qwen"]["audio_padding_seconds"] == 0.45
    assert len(saved_findings) == 1
    assert saved_findings[0].selected is True
