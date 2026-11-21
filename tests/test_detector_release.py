"""Resident detectors are released once, before deferred descriptions load another model."""

import pytest

from scanner.models import FindingCreate, McfCategoryFamily, Severity
from scanner.pipeline import d1_detector, detector_memory, qwen_summary
from scanner.pipeline.runner import _scan_visual_by_scene
from scanner.pipeline.scenes import Scene

QWEN_8B = "qwen3-vl-8b-instruct-4bit"
QWEN_4B = "qwen3-vl-4b-instruct-4bit"


@pytest.mark.parametrize(
    ("video_model", "qwen_model", "deferred"),
    [
        ("d1-3b", QWEN_8B, True),
        ("qwen3-vl-4b", QWEN_8B, True),
        ("shieldstral", QWEN_4B, True),
        ("qwen3-vl-8b", QWEN_8B, False),
        ("qwen3-vl-4b", QWEN_4B, False),
        ("nudenet", QWEN_8B, False),
        (None, QWEN_8B, False),
    ],
)
def test_descriptions_wait_only_for_a_separate_resident_detector(video_model, qwen_model, deferred):
    assert detector_memory.defer_descriptions(video_model, qwen_model) is deferred


def _run(monkeypatch, tmp_path, video_model, events, scenes=3):
    scene_list = [
        Scene(start_seconds=i * 10.0, end_seconds=i * 10.0 + 10, index=i) for i in range(scenes)
    ]
    monkeypatch.setattr("scanner.pipeline.runner.detect_scenes", lambda *_a, **_k: scene_list)
    monkeypatch.setattr("scanner.pipeline.runner.get_video_frame_interval", lambda *_a: 1.0)

    def sample(_video, scene, output_dir, interval_seconds, **_kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame.jpg"
        frame.write_bytes(b"jpg")
        return [(frame, scene.start_seconds)]

    def scan_frames(frames, video_id, **_kwargs):
        events.append(("detect", frames[0][1]))
        return [
            FindingCreate(
                video_id=video_id, category=McfCategoryFamily.VIOLENCE, subcategory="violence",
                start_seconds=frames[0][1], end_seconds=frames[0][1] + 1,
                severity=Severity.LOW, severity_source="detector", confidence=0.9,
                label="d1_violence", source="visual",
            )
        ]

    def summarize(findings, _frames, **_kwargs):
        events.append(("describe", findings[0].start_seconds))
        return findings

    monkeypatch.setattr("scanner.pipeline.runner.sample_scene_frames", sample)
    monkeypatch.setattr("scanner.pipeline.runner.scan_frames", scan_frames)
    monkeypatch.setattr("scanner.pipeline.runner.summarize_flagged_segments", summarize)
    monkeypatch.setattr(
        "scanner.pipeline.runner_scenes.release_detector",
        lambda model: events.append(("release", model)),
    )
    progress = []
    findings = _scan_visual_by_scene(
        "video.mp4", "video-1", tmp_path, coarse_scan_enabled=False,
        coarse_scan_interval_seconds=None, qwen_model=QWEN_8B, video_model=video_model,
        progress_callback=lambda fraction, _message: progress.append(fraction),
    )
    return findings, progress


def test_d1_detects_every_scene_then_releases_once_before_describing(monkeypatch, tmp_path):
    events: list = []

    findings, progress = _run(monkeypatch, tmp_path, "d1-3b", events)

    assert events == [
        ("detect", 0.0), ("detect", 10.0), ("detect", 20.0),
        ("release", "d1-3b"),
        ("describe", 0.0), ("describe", 10.0), ("describe", 20.0),
    ]
    assert [finding.start_seconds for finding in findings] == [0.0, 10.0, 20.0]
    assert progress == sorted(progress)


def test_shared_qwen_detector_keeps_per_scene_descriptions_without_release(monkeypatch, tmp_path):
    events: list = []

    _run(monkeypatch, tmp_path, "qwen3-vl-8b", events, scenes=2)

    assert events == [("detect", 0.0), ("describe", 0.0), ("detect", 10.0), ("describe", 10.0)]


def test_cached_detections_resume_descriptions_without_detecting_again(monkeypatch, tmp_path):
    first: list = []
    _run(monkeypatch, tmp_path, "d1-3b", first, scenes=2)
    for path in (tmp_path / "visual-results").glob("scene_????.json"):
        path.unlink()  # descriptions were interrupted; detections remain cached
    resumed: list = []

    findings, _progress = _run(monkeypatch, tmp_path, "d1-3b", resumed, scenes=2)

    assert resumed == [("release", "d1-3b"), ("describe", 0.0), ("describe", 10.0)]
    assert len(findings) == 2


def test_release_drops_only_the_separate_detector_model(monkeypatch):
    cleared = []
    monkeypatch.setattr(detector_memory, "_clear_mlx_cache", lambda: cleared.append(True))
    monkeypatch.setattr(qwen_summary, "_CACHE", {QWEN_4B: "detector", QWEN_8B: "description"})

    detector_memory.release_detector("qwen3-vl-4b")

    assert qwen_summary._CACHE == {QWEN_8B: "description"}
    assert cleared == [True]

    monkeypatch.setattr(d1_detector, "_CACHE", {"model": object()})
    detector_memory.release_detector("d1-3b")
    assert d1_detector._CACHE == {}
    assert d1_detector.release() is False
