from pathlib import Path

from scanner.models import FindingCreate, McfCategoryFamily, Severity
from scanner.pipeline.runner import _scan_visual_by_scene
from scanner.pipeline.scenes import Scene


def test_coarse_scan_skips_fine_pass_on_scenes_with_clean_samples(monkeypatch, tmp_path):
    scenes = [
        Scene(start_seconds=0.0, end_seconds=10.0, index=0),
        Scene(start_seconds=10.0, end_seconds=20.0, index=1),
    ]
    sampled: list[tuple[int, float | None]] = []

    def sample(_video_path, scene, _output_dir, interval_seconds, **_kwargs):
        sampled.append((scene.index, interval_seconds))
        return [(tmp_path / "frame.jpg", scene.start_seconds)]

    monkeypatch.setattr("scanner.pipeline.runner.detect_scenes", lambda *_a, **_k: scenes)
    monkeypatch.setattr("scanner.pipeline.runner.get_video_frame_interval", lambda *_a: 1 / 24)
    monkeypatch.setattr("scanner.pipeline.runner.sample_scene_frames", sample)
    monkeypatch.setattr("scanner.pipeline.runner.scan_frame", lambda _path, time, **_k: time == 10)
    monkeypatch.setattr("scanner.pipeline.runner.scan_frames", lambda *_a, **_k: [])

    _scan_visual_by_scene(
        "video.mp4", "video-1", tmp_path, coarse_scan_enabled=True, coarse_scan_interval_seconds=2.5
    )

    assert sampled == [(0, 2.5), (1, 2.5), (1, None)]


def test_coarse_scan_falls_back_to_every_frame_when_no_coarse_frames(monkeypatch, tmp_path: Path):
    scene = Scene(start_seconds=0.0, end_seconds=1.0, index=0)
    calls: list[tuple[str, float | None, float | None]] = []

    monkeypatch.setattr("scanner.pipeline.runner.detect_scenes", lambda *_args, **_kwargs: [scene])
    monkeypatch.setattr("scanner.pipeline.runner.get_video_frame_interval", lambda *_args: 1 / 24)

    def fake_sample_scene_frames(_video_path, _scene, output_dir, interval_seconds, **kwargs):
        kind = "coarse" if output_dir.parent.name == "coarse" else "fine"
        calls.append((kind, interval_seconds, kwargs.get("timestamp_interval_seconds")))
        return [] if kind == "coarse" else [(tmp_path / "frame.jpg", 0.0)]

    def fake_scan_frames(frames, video_id, **kwargs):
        assert frames and video_id == "video-1"
        assert kwargs["frame_interval_seconds"] == 1 / 24
        return [
            FindingCreate(
                video_id=video_id,
                category=McfCategoryFamily.NUDITY,
                subcategory="nudity",
                start_seconds=0.0,
                end_seconds=1 / 24,
                severity=Severity.MEDIUM,
                confidence=0.9,
                label="FEMALE_BREAST_EXPOSED",
                details="test",
                source="visual",
            )
        ]

    monkeypatch.setattr("scanner.pipeline.runner.sample_scene_frames", fake_sample_scene_frames)
    monkeypatch.setattr("scanner.pipeline.runner.scan_frames", fake_scan_frames)
    monkeypatch.setattr(
        "scanner.pipeline.runner.summarize_flagged_segments",
        lambda findings, *_args, **_kwargs: findings,
    )

    findings = _scan_visual_by_scene(
        "video.mp4",
        "video-1",
        tmp_path,
        coarse_scan_enabled=True,
        coarse_scan_interval_seconds=6,
    )

    assert len(findings) == 1
    assert calls == [("coarse", 6, None), ("fine", None, 1 / 24)]


def test_disabled_coarse_scan_fine_scans_every_scene_without_coarse_sampling(
    monkeypatch, tmp_path: Path
):
    scene = Scene(start_seconds=0.0, end_seconds=10.0, index=0)
    sampled_intervals: list[float | None] = []

    monkeypatch.setattr("scanner.pipeline.runner.detect_scenes", lambda *_args, **_kwargs: [scene])
    monkeypatch.setattr("scanner.pipeline.runner.get_video_frame_interval", lambda *_args: 1 / 30)

    def fake_sample_scene_frames(_video_path, _scene, _output_dir, interval_seconds, **_kwargs):
        sampled_intervals.append(interval_seconds)
        return [(tmp_path / "frame.jpg", 0.0)]

    monkeypatch.setattr("scanner.pipeline.runner.sample_scene_frames", fake_sample_scene_frames)
    monkeypatch.setattr("scanner.pipeline.runner.scan_frames", lambda *_args, **_kwargs: [])

    findings = _scan_visual_by_scene(
        "video.mp4",
        "video-1",
        tmp_path,
        coarse_scan_enabled=False,
        coarse_scan_interval_seconds=None,
    )

    assert findings == []
    assert sampled_intervals == [None]


def test_scene_scans_reuse_cached_boundaries(monkeypatch, tmp_path: Path):
    scene = Scene(start_seconds=0.0, end_seconds=10.0, index=0)
    sampled_intervals: list[float | None] = []

    monkeypatch.setattr(
        "scanner.pipeline.runner.detect_scenes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use cached scenes")),
    )
    monkeypatch.setattr("scanner.pipeline.runner.get_video_frame_interval", lambda *_args: 1 / 30)
    def fake_sample_scene_frames(_video_path, _scene, _output_dir, interval_seconds, **_kwargs):
        sampled_intervals.append(interval_seconds)
        return []

    monkeypatch.setattr("scanner.pipeline.runner.sample_scene_frames", fake_sample_scene_frames)

    findings = _scan_visual_by_scene(
        "video.mp4",
        "video-1",
        tmp_path,
        coarse_scan_enabled=False,
        coarse_scan_interval_seconds=None,
        cached_scenes=[scene],
    )

    assert findings == []
    assert sampled_intervals == [None]
