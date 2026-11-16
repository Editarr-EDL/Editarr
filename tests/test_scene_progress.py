from pathlib import Path

import pytest

from scanner.pipeline import processes, scenes


def test_scene_frame_extraction_reports_ffmpeg_time_progress(monkeypatch, tmp_path: Path):
    progress: list[tuple[float, str]] = []
    command: list[str] = []

    def fake_run_command(args, **kwargs):
        command.extend(args)
        callback = kwargs.get("output_callback")
        if callback:
            callback("out_time_us=5000000")
            callback("out_time_us=20000000")

    monkeypatch.setattr(scenes, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(processes, "run_command", fake_run_command)

    scenes.sample_scene_frames(
        "example.mkv",
        scenes.Scene(start_seconds=30, end_seconds=50, index=2),
        tmp_path,
        interval_seconds=2,
        progress_callback=lambda fraction, message: progress.append((fraction, message)),
    )

    assert command[command.index("-progress") + 1] == "pipe:2"
    assert command[command.index("-pix_fmt") + 1] == "yuvj420p"
    assert [fraction for fraction, _message in progress] == pytest.approx([0.25, 1.0])
    assert [message for _fraction, message in progress] == [
        "Extracting scene frames (5s/20s)",
        "Extracting scene frames (20s/20s)",
    ]


def test_short_scene_falls_back_to_one_frame(monkeypatch, tmp_path: Path):
    commands: list[list[str]] = []

    def fake_run_command(args, **_kwargs):
        commands.append(args)
        if "-frames:v" in args:
            Path(args[-2]).write_bytes(b"jpeg")

    monkeypatch.setattr(scenes, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(processes, "run_command", fake_run_command)

    frames = scenes.sample_scene_frames(
        "example.mkv",
        scenes.Scene(start_seconds=30, end_seconds=32, index=2),
        tmp_path,
        interval_seconds=15,
    )

    assert len(commands) == 2
    assert "-frames:v" in commands[1]
    assert frames == [(tmp_path / "scene_0002_fallback.jpg", 30)]
