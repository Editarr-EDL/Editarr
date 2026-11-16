import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.runtime import mute_preview


def test_mute_preview_fast_seeks_to_one_exact_filter_range(monkeypatch):
    command: list[str] = []
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg"),
    )
    monkeypatch.setattr(
        mute_preview.subprocess,
        "run",
        lambda args, **_kwargs: (command.extend(args) or SimpleNamespace(stdout=b"MP3")),
    )

    assert mute_preview.render_mute_preview("example.mkv", 4, 5.75) == b"MP3"

    assert command[command.index("-ss") + 1] == "4.000"
    assert command[command.index("-t") + 1] == "1.750"
    assert "-filter_complex" not in command
    assert command[command.index("-c:a") + 1] == "libmp3lame"
    assert command[command.index("-b:a") + 1] == "128k"
    assert command[command.index("-f") + 1] == "mp3"


def test_mute_preview_requires_a_valid_range():
    with pytest.raises(ValueError, match="valid time range"):
        mute_preview.render_mute_preview("example.mkv", 2, 2)


def test_mute_preview_has_a_one_second_minimum_duration(monkeypatch):
    command: list[str] = []
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg"),
    )
    monkeypatch.setattr(
        mute_preview.subprocess,
        "run",
        lambda args, **_kwargs: (command.extend(args) or SimpleNamespace(stdout=b"MP3")),
    )

    mute_preview.render_mute_preview("example.mkv", 4, 4.2)

    assert command[command.index("-ss") + 1] == "3.600"
    assert command[command.index("-t") + 1] == "1.000"


def test_video_preview_fast_seeks_to_one_exact_filter_range(monkeypatch):
    command: list[str] = []
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg"),
    )
    monkeypatch.setattr(mute_preview, "playable_video_stream_index", lambda _path: 1)
    def render_to_temp_file(args, **_kwargs):
        command.extend(args)
        Path(args[-1]).write_bytes(b"MP4")
        return SimpleNamespace(stdout=b"")

    monkeypatch.setattr(mute_preview.subprocess, "run", render_to_temp_file)

    assert mute_preview.render_video_preview("example.mkv", 4, 5.75) == b"MP4"

    assert command[command.index("-ss") + 1] == "4.000"
    assert command[command.index("-t") + 1] == "1.750"
    assert command[command.index("-map") + 1] == "0:1"
    assert command[command.index("-vf") + 1] == (
        "scale='min(1280,iw)':'min(720,ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-tag:v") + 1] == "avc1"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-f") + 1] == "mp4"
    assert command[command.index("-movflags") + 1] == "+faststart"


def test_mute_preview_surfaces_ffmpeg_errors(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg"),
    )
    monkeypatch.setattr(
        mute_preview.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, "ffmpeg", stderr=b"invalid media")
        ),
    )

    with pytest.raises(ValueError, match="invalid media"):
        mute_preview.render_mute_preview("example.mkv", 1, 2)
