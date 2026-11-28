"""Resident detectors are released once, before deferred descriptions load another model."""

import gc
import json
import weakref
from dataclasses import fields

import pytest

from scanner.models import FindingCreate, McfCategoryFamily, Severity
from scanner.pipeline import d1_detector, detector_memory, pending_scenes, qwen_summary
from scanner.pipeline.runner import _scan_visual_by_scene
from scanner.pipeline.scenes import Scene
from scanner.settings.capabilities import QWEN38_27B_MODEL, QWEN38_27B_VIDEO_MODEL

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
        (QWEN38_27B_VIDEO_MODEL, QWEN38_27B_MODEL, False),
        (QWEN38_27B_VIDEO_MODEL, QWEN_8B, True),
        ("qwen3-vl-8b", QWEN38_27B_MODEL, True),
        ("nudenet", QWEN_8B, False),
        (None, QWEN_8B, False),
    ],
)
def test_descriptions_wait_only_for_a_separate_resident_detector(video_model, qwen_model, deferred):
    assert detector_memory.defer_descriptions(video_model, qwen_model) is deferred


class _Tracked(list):
    """A list that can be weakly referenced, to prove the runner drops it."""


def _run(
    monkeypatch, tmp_path, video_model, events, scenes=3, cached=False, tracked=None,
    on_describe=None, release=None,
):
    scene_list = [
        Scene(start_seconds=i * 10.0, end_seconds=i * 10.0 + 10, index=i) for i in range(scenes)
    ]

    def detect_scenes(*_args, progress_callback=None, **_kwargs):
        for fraction in (0.0, 0.5, 1.0):
            if progress_callback:
                progress_callback(fraction, "Detecting scene boundaries")
        return scene_list

    monkeypatch.setattr("scanner.pipeline.runner.detect_scenes", detect_scenes)
    monkeypatch.setattr("scanner.pipeline.runner.get_video_frame_interval", lambda *_a: 1.0)

    def sample(_video, scene, output_dir, interval_seconds, **_kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        frames = _Tracked()
        for offset in range(3):
            frame = output_dir / f"frame_{offset}.jpg"
            frame.write_bytes(b"jpg")
            frames.append((frame, scene.start_seconds + offset))
        if tracked is not None:
            tracked.append(weakref.ref(frames))
        return frames

    def scan_frames(frames, video_id, **_kwargs):
        events.append(("detect", frames[0][1]))
        detected = _Tracked(
            FindingCreate(
                video_id=video_id, category=McfCategoryFamily.VIOLENCE, subcategory="violence",
                start_seconds=stamp, end_seconds=stamp + 1,
                severity=Severity.LOW, severity_source="detector", confidence=0.9,
                label="d1_violence", source="visual",
            )
            for _frame, stamp in frames[:2]  # the last frame is clean
        )
        if tracked is not None:
            tracked.append(weakref.ref(detected))
        return detected

    def summarize(findings, frames, **_kwargs):
        events.append(("describe", findings[0].start_seconds))
        if on_describe:
            on_describe(findings[0].start_seconds)
        paths = {stamp: path for path, stamp in frames}
        # Describe with the frame each finding maps to, so frame hand-off is checked too.
        return [
            finding.model_copy(update={"details": paths[finding.start_seconds].name})
            for finding in findings
        ]

    monkeypatch.setattr("scanner.pipeline.runner.sample_scene_frames", sample)
    monkeypatch.setattr("scanner.pipeline.runner.scan_frames", scan_frames)
    monkeypatch.setattr("scanner.pipeline.runner.summarize_flagged_segments", summarize)
    monkeypatch.setattr(
        "scanner.pipeline.runner_scenes.release_detector",
        release or (lambda model: events.append(("release", model))),
    )
    progress = []
    findings = _scan_visual_by_scene(
        "video.mp4", "video-1", tmp_path, coarse_scan_enabled=False,
        coarse_scan_interval_seconds=None, qwen_model=QWEN_8B, video_model=video_model,
        progress_callback=lambda fraction, _message: progress.append(fraction),
        cached_scenes=scene_list if cached else None,
    )
    return findings, progress


@pytest.mark.parametrize("cached", [False, True])
def test_d1_detects_every_scene_then_releases_once_before_describing(
    monkeypatch, tmp_path, cached
):
    events: list = []

    findings, progress = _run(monkeypatch, tmp_path, "d1-3b", events, cached=cached)

    assert events == [
        ("detect", 0.0), ("detect", 10.0), ("detect", 20.0),
        ("release", "d1-3b"),
        ("describe", 0.0), ("describe", 10.0), ("describe", 20.0),
    ]
    assert [finding.start_seconds for finding in findings] == [0.0, 1.0, 10.0, 11.0, 20.0, 21.0]
    # Scene-boundary detection is scaled too, so progress never moves backwards.
    assert all(later >= earlier for earlier, later in zip(progress, progress[1:]))
    assert progress[-1] == pytest.approx(1.0)


def test_shared_qwen_detector_keeps_per_scene_descriptions_without_release(monkeypatch, tmp_path):
    events: list = []

    _, progress = _run(monkeypatch, tmp_path, "qwen3-vl-8b", events, scenes=2)

    assert events == [("detect", 0.0), ("describe", 0.0), ("detect", 10.0), ("describe", 10.0)]
    assert all(later >= earlier for earlier, later in zip(progress, progress[1:]))


def test_deferred_findings_match_per_scene_descriptions(monkeypatch, tmp_path):
    deferred, _ = _run(monkeypatch, tmp_path / "deferred", "d1-3b", [])
    direct, _ = _run(monkeypatch, tmp_path / "direct", "nudenet", [])

    assert [finding.model_dump() for finding in deferred] == [
        finding.model_dump() for finding in direct
    ]
    assert [finding.details for finding in deferred] == ["frame_0.jpg", "frame_1.jpg"] * 3


def test_detected_scenes_are_spooled_and_only_an_index_is_held(monkeypatch, tmp_path):
    tracked: list = []
    entries: list = []
    seen: dict = {}
    spool = pending_scenes.spool_scene
    monkeypatch.setattr(
        pending_scenes,
        "spool_scene",
        lambda *args: entries.append(spool(*args)) or entries[-1],
    )
    results = tmp_path / "visual-results"

    def release(_model):
        gc.collect()
        seen["alive"] = [ref for ref in tracked if ref() is not None]
        seen["pending"] = sorted(path.name for path in (results / "pending").iterdir())
        seen["completed"] = sorted(path.name for path in results.glob("*.json"))

    _run(monkeypatch, tmp_path, "d1-3b", [], tracked=tracked, release=release)

    # Frame lists and per-frame findings were dropped once spooled; only the index remains.
    assert len(tracked) == 6 and seen["alive"] == []
    assert {field.name for field in fields(pending_scenes.PendingScene)} == {
        "order", "scene_index", "path",
    }
    assert [(entry.order, entry.scene_index) for entry in entries] == [(1, 0), (2, 1), (3, 2)]
    assert seen["pending"] == ["scene_0000.json", "scene_0001.json", "scene_0002.json"]
    assert seen["completed"] == []
    # Afterwards every spooled file is consumed and only the completed caches remain.
    assert not (results / "pending").exists()
    assert sorted(path.name for path in results.glob("*.json")) == [
        "scene_0000.json", "scene_0001.json", "scene_0002.json",
    ]


def test_spooled_scenes_are_loaded_and_deleted_one_at_a_time(monkeypatch, tmp_path):
    loads: list = []
    load = pending_scenes.load_scene

    def recording_load(entry):
        remaining = sorted(path.name for path in entry.path.parent.iterdir())
        loads.append((entry.scene_index, remaining))
        return load(entry)

    monkeypatch.setattr(pending_scenes, "load_scene", recording_load)

    _run(monkeypatch, tmp_path, "d1-3b", [])

    assert loads == [
        (0, ["scene_0000.json", "scene_0001.json", "scene_0002.json"]),
        (1, ["scene_0001.json", "scene_0002.json"]),
        (2, ["scene_0002.json"]),
    ]


def test_spooled_scene_keeps_only_frames_that_findings_reference(tmp_path):
    finding = FindingCreate(
        video_id="v", category=McfCategoryFamily.VIOLENCE, subcategory="violence",
        start_seconds=1.0, source="visual",
    )
    frames = [(tmp_path / "a.jpg", 0.0), (tmp_path / "b.jpg", 1.0)]
    entry = pending_scenes.spool_scene(tmp_path, 1, 7, frames, [finding])

    assert entry.path == tmp_path / "pending" / "scene_0007.json"
    assert json.loads(entry.path.read_text())["version"] == pending_scenes.PENDING_SCENE_VERSION
    assert pending_scenes.load_scene(entry) == ([(tmp_path / "b.jpg", 1.0)], [finding])
    entry.path.write_text("{not json")
    with pytest.raises(RuntimeError, match="Pending visual scene 7 could not be read"):
        pending_scenes.load_scene(entry)


def test_failed_description_leaves_no_pending_scene_files(monkeypatch, tmp_path):
    def fail_on_second(start_seconds):
        if start_seconds == 10.0:
            raise RuntimeError("description failed")

    with pytest.raises(RuntimeError, match="description failed"):
        _run(monkeypatch, tmp_path, "d1-3b", [], on_describe=fail_on_second)

    results = tmp_path / "visual-results"
    assert not (results / "pending").exists()
    assert sorted(path.name for path in results.glob("*.json")) == ["scene_0000.json"]


@pytest.mark.parametrize(
    ("video_model", "artifact"),
    [("qwen3-vl-4b", QWEN_4B), (QWEN38_27B_VIDEO_MODEL, QWEN38_27B_MODEL)],
)
def test_release_drops_only_the_separate_detector_model(monkeypatch, video_model, artifact):
    cleared = []
    monkeypatch.setattr(detector_memory, "_clear_mlx_cache", lambda: cleared.append(True))
    monkeypatch.setattr(qwen_summary, "_CACHE", {artifact: "detector", QWEN_8B: "description"})

    detector_memory.release_detector(video_model)

    assert qwen_summary._CACHE == {QWEN_8B: "description"}
    assert cleared == [True]


def test_release_drops_the_d1_detector(monkeypatch):
    monkeypatch.setattr(detector_memory, "_clear_mlx_cache", lambda: None)
    monkeypatch.setattr(d1_detector, "_CACHE", {"model": object()})
    detector_memory.release_detector("d1-3b")
    assert d1_detector._CACHE == {}
    assert d1_detector.release() is False
