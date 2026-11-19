import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scanner.pipeline import processes, scenes
from scanner.runtime.runtime_support import resolve_ffmpeg

FRAME_RATE = 5
LUMA_STEP = 6


def test_interval_sampling_selects_frames_at_bin_midpoints(monkeypatch, tmp_path: Path):
    commands: list[list[str]] = []

    def fake_run_command(args, **_kwargs):
        commands.append(args)
        for number in (1, 2, 3):
            Path(args[-2].replace("%06d", f"{number:06d}")).write_bytes(b"jpeg")

    monkeypatch.setattr(scenes, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(processes, "run_command", fake_run_command)

    frames = scenes.sample_scene_frames(
        "example.mkv",
        scenes.Scene(start_seconds=30, end_seconds=42, index=2),
        tmp_path,
        interval_seconds=5.0,
    )

    command = commands[-1]
    video_filter = command[command.index("-vf") + 1]
    assert video_filter.startswith("select=gte(t\\,2.499+selected_n*5.0),")
    assert "fps=" not in video_filter
    assert command[command.index("-fps_mode") + 1] == "vfr"
    assert [timestamp for _path, timestamp in frames] == [32.5, 37.5]


@pytest.fixture
def numbered_video(tmp_path: Path) -> Path:
    """Six-second clip whose luma encodes each frame's number."""
    path = tmp_path / "numbered.mkv"
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:size=64x48:rate={FRAME_RATE}:duration=6,format=yuv420p,"
            f"geq=lum='16+N*{LUMA_STEP}':cb=128:cr=128",
            "-c:v",
            "mpeg4",
            "-q:v",
            "2",
            "-g",
            "12",
            str(path),
            "-y",
        ],
        check=True,
        capture_output=True,
    )
    return path


def _shown_frame_seconds(path: Path) -> float:
    """Decode the source time encoded in a full-range JPEG's luma."""
    full_range = float(np.asarray(Image.open(path).convert("L"), dtype=np.float32).mean())
    limited = 16 + full_range * 219 / 255
    return round((limited - 16) / LUMA_STEP) / FRAME_RATE


@pytest.mark.parametrize(
    ("start", "end", "interval", "expected", "shown_frames"),
    [
        # Bins [1.2, 2.0), [2.0, 2.8), [2.8, 3.6) are sampled at their midpoints;
        # the partial bin's midpoint (4.0) would fall past the scene end.
        (1.2, 3.8, 0.8, [1.6, 2.4, 3.2], [1.6, 2.4, 3.2]),
        # A scene shorter than the interval yields only its midpoint frame.
        (1.0, 1.8, 5.0, [1.4], [1.4]),
        # A midpoint between source frames yields the next frame.
        (1.0, 1.6, 5.0, [1.3], [1.4]),
    ],
)
def test_interval_samples_show_their_timestamps_inside_the_scene(
    numbered_video: Path, tmp_path: Path, start, end, interval, expected, shown_frames
):
    frames = scenes.sample_scene_frames(
        str(numbered_video),
        scenes.Scene(start_seconds=start, end_seconds=end, index=0),
        tmp_path / "frames",
        interval_seconds=interval,
    )

    assert [timestamp for _path, timestamp in frames] == pytest.approx(expected)
    for frame_path, timestamp in frames:
        shown = _shown_frame_seconds(frame_path)
        assert start <= shown < end
        assert timestamp - 0.001 <= shown < timestamp + 1 / FRAME_RATE
    assert [_shown_frame_seconds(path) for path, _ in frames] == pytest.approx(shown_frames)
