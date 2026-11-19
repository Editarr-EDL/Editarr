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

    def fake_run_command(args, *, output_callback=None, **_kwargs):
        commands.append(args)
        output_callback("[Parsed_showinfo_1 @ 0x1] config in time_base: 1/1000, frame_rate: 5/1")
        for number, pts in ((1, 2500), (2, 7500), (3, 12500)):
            output_callback(f"[Parsed_showinfo_1 @ 0x1] n: {number - 1} pts: {pts} pts_time:0")
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
    assert video_filter.startswith("select=gte(t\\,2.499+selected_n*5.0),showinfo,")
    assert "fps=" not in video_filter
    assert command[command.index("-fps_mode") + 1] == "vfr"
    assert [timestamp for _path, timestamp in frames] == [32.5, 37.5]


def _write_numbered_video(
    path: Path, frame_rate: int, duration: float, pts_expression: str | None = None
) -> Path:
    """Write a clip whose luma encodes each frame's number.

    ``pts_expression`` optionally retimes frames (``setpts``) to build gaps.
    """
    video_filter = (
        f"color=c=black:size=64x48:rate={frame_rate}:duration={duration},format=yuv420p,"
        f"geq=lum='16+N*{LUMA_STEP}':cb=128:cr=128"
    )
    if pts_expression:
        video_filter += f",setpts='{pts_expression}'"
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-f",
            "lavfi",
            "-i",
            video_filter,
            "-c:v",
            "mpeg4",
            "-q:v",
            "2",
            "-g",
            "12",
            "-fps_mode",
            "vfr",
            str(path),
            "-y",
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_cpu_retry_discards_frames_and_times_from_failed_hardware_attempt(
    monkeypatch, tmp_path: Path
):
    def fake_run_command(args, *, output_callback=None, **_kwargs):
        hardware = "-hwaccel" in args
        output_callback("[Parsed_showinfo_1 @ 0x1] config in time_base: 1/1000, frame_rate: 5/1")
        for number, pts in ((1, 600), (2, 1400)) if hardware else ((1, 2500),):
            output_callback(f"[Parsed_showinfo_1 @ 0x1] n: {number - 1} pts: {pts} pts_time:0")
            Path(args[-2].replace("%06d", f"{number:06d}")).write_bytes(b"jpeg")
        if hardware:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(scenes, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(processes.sys, "platform", "darwin")
    monkeypatch.setattr(processes, "run_command", fake_run_command)

    frames = scenes.sample_scene_frames(
        "example.mkv",
        scenes.Scene(start_seconds=30, end_seconds=42, index=2),
        tmp_path,
        interval_seconds=5.0,
    )

    assert frames == [(tmp_path / "scene_0002_000001.jpg", 32.5)]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["scene_0002_000001.jpg"]


def test_showinfo_times_survive_split_prefixes_and_ignore_other_output():
    times = scenes._ShowinfoTimes()
    for line in (
        # Echoed input header: before the time base, and not at a line start.
        "Input #0, mov,mp4, from '/media/clip n: 0 pts: 5 pts_time:9.mp4':",
        "    title           : config in time_base: 1/7",
        "n:   0 pts:      5 pts_time:9.mp4",
        "[Parsed_showinfo_1 @ 0x1] config in time_base: 1/1000, frame_rate: 10/1",
        "[Parsed_showinfo_1 @ 0x1] config out time_base: 0/0, frame_rate: 0/0",
        "[Parsed_showinfo_1 @ 0x1] n:   0 pts:    100 pts_time:0.1     duration:    100",
        "[image2 @ 0x2] frame=    1 fps=0.0 q=5.0 size=N/A time=00:00:00.10",
        "n:   1 pts:  12345 pts_time:12.3450 duration:    100",
        "out_time_us=200000",
        "[Parsed_showinfo_1 @ 0x1] color_range:tv color_space:unknown",
        # Output header echoed after frames start, and a later time base.
        "    title           : n: 0 pts: 2400 pts_time:0.1",
        "[Parsed_showinfo_1 @ 0x1] config in time_base: 1/7, frame_rate: 10/1",
        "[Parsed_showinfo_1 @ 0x1] n:   2 pts:    300 pts_time:0.3     duration:    100",
    ):
        times.parse(line)

    assert times.seconds == pytest.approx([0.1, 12.345, 0.3])


@pytest.mark.parametrize(
    ("name", "title"),
    [
        ("titled.mkv", "n: 0 pts: 2400 pts_time:0.1"),
        ("titled.mkv", "config in time_base: 1/7"),
        ("clip n: 0 pts: 5 pts_time:9.mkv", None),
    ],
    ids=["frame-like-title", "time-base-like-title", "frame-like-filename"],
)
def test_echoed_input_names_and_metadata_do_not_change_sample_times(
    tmp_path: Path, name, title
):
    source = _write_numbered_video(tmp_path / "source.mkv", 10, 2)
    video = tmp_path / name
    metadata = ["-metadata", f"title={title}"] if title else []
    subprocess.run(
        [resolve_ffmpeg(), "-i", str(source), "-c", "copy", *metadata, str(video), "-y"],
        check=True,
        capture_output=True,
    )

    frames = scenes.sample_scene_frames(
        str(video),
        scenes.Scene(start_seconds=0.0, end_seconds=1.0, index=0),
        tmp_path / "frames",
        interval_seconds=0.2,
    )

    timestamps = [timestamp for _path, timestamp in frames]
    assert timestamps == pytest.approx([0.1, 0.3, 0.5, 0.7, 0.9])
    assert [_shown_frame_number(path) / 10 for path, _ in frames] == pytest.approx(timestamps)


@pytest.fixture
def numbered_video(tmp_path: Path) -> Path:
    """Six-second 5 fps clip whose luma encodes each frame's number."""
    return _write_numbered_video(tmp_path / "numbered.mkv", FRAME_RATE, 6)


def _shown_frame_number(path: Path) -> int:
    """Decode the source frame number encoded in a full-range JPEG's luma."""
    full_range = float(np.asarray(Image.open(path).convert("L"), dtype=np.float32).mean())
    limited = 16 + full_range * 219 / 255
    return round((limited - 16) / LUMA_STEP)


def _shown_frame_seconds(path: Path) -> float:
    return _shown_frame_number(path) / FRAME_RATE


@pytest.mark.parametrize(
    ("start", "end", "interval", "expected", "shown_frames"),
    [
        # Bins [1.2, 2.0), [2.0, 2.8), [2.8, 3.6) are sampled at their midpoints;
        # the partial bin's midpoint (4.0) would fall past the scene end.
        (1.2, 3.8, 0.8, [1.6, 2.4, 3.2], [1.6, 2.4, 3.2]),
        # A scene shorter than the interval yields only its midpoint frame.
        (1.0, 1.8, 5.0, [1.4], [1.4]),
        # A midpoint between source frames yields the next frame, at its own time.
        (1.0, 1.6, 5.0, [1.4], [1.4]),
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


def test_interval_shorter_than_source_cadence_keeps_frame_times(tmp_path: Path):
    video = _write_numbered_video(tmp_path / "ten_fps.mkv", 10, 2)

    frames = scenes.sample_scene_frames(
        str(video),
        scenes.Scene(start_seconds=0.0, end_seconds=1.0, index=0),
        tmp_path / "frames",
        interval_seconds=0.05,
    )

    # Every 0.05 s tick needs a new frame, so the 10 fps source supplies each
    # remaining frame once, stamped with its own time rather than the tick.
    timestamps = [timestamp for _path, timestamp in frames]
    assert timestamps == pytest.approx([n / 10 for n in range(1, 10)])
    assert [_shown_frame_number(path) / 10 for path, _ in frames] == pytest.approx(timestamps)


def test_interval_samples_after_a_source_gap_keep_frame_times(tmp_path: Path):
    # Frames 0-9 play at 0.0-0.9 s; frames 10-19 resume at 2.0-2.9 s.
    video = _write_numbered_video(
        tmp_path / "gap.mkv", 10, 2, pts_expression="(N+gte(N,10)*10)/10/TB"
    )

    frames = scenes.sample_scene_frames(
        str(video),
        scenes.Scene(start_seconds=0.0, end_seconds=3.0, index=0),
        tmp_path / "frames",
        interval_seconds=0.4,
    )

    timestamps = [timestamp for _path, timestamp in frames]
    shown = [_shown_frame_number(path) for path, _ in frames]
    assert timestamps == pytest.approx([(n + (n >= 10) * 10) / 10 for n in shown])
    assert timestamps[:3] == pytest.approx([0.2, 0.6, 2.0])
    assert timestamps == sorted(set(timestamps))
