"""Extract frames from video files at a given interval."""

from collections.abc import Callable, Iterable
from pathlib import Path

from scanner.pipeline.processes import run_command, run_ffmpeg_with_optional_macos_hwaccel
from scanner.runtime.runtime_support import resolve_ffmpeg
from scanner.settings.config import settings

from .audio_tracks import PROBE_OPTIONS
from .bounded_media import BoundedInput

_ATTACHED_PICTURE_DISPOSITION = 1 << 10


def extracted_frame_scale_filter() -> str:
    """Return the FFmpeg scale filter used for visual-model source frames."""
    max_height = settings.extracted_frame_max_height
    if max_height <= 0:
        raise ValueError("Extracted frame max height must be greater than zero")
    return f"scale=-2:min({max_height}\\,ih)"


def _playable_video_streams(streams: Iterable[object]) -> list[object]:
    """Return video streams after excluding embedded cover art."""
    return [
        stream
        for stream in streams
        if not (int(getattr(stream, "disposition", 0)) & _ATTACHED_PICTURE_DISPOSITION)
    ]


def _playable_video_stream_index(streams: Iterable[object]) -> int | None:
    """Return the first non-cover-art stream's FFmpeg stream index."""
    playable_streams = _playable_video_streams(streams)
    if not playable_streams:
        return None
    return int(getattr(playable_streams[0], "index"))


def playable_video_stream_index(video_path: str) -> int | None:
    """Return the physical stream index of the first playable video stream."""
    try:
        import av

        with av.open(video_path) as container:
            return _playable_video_stream_index(container.streams.video)
    except (ImportError, OSError, ValueError):
        return None


def _positive_stream_frame_rate(streams: Iterable[object]) -> float | None:
    """Find a usable frame rate while ignoring embedded cover-art streams."""
    for stream in _playable_video_streams(streams):
        for rate in (getattr(stream, "average_rate", None), getattr(stream, "base_rate", None)):
            if rate is not None and float(rate) > 0:
                return float(rate)
    return None


def _positive_stream_duration(streams: Iterable[object]) -> float | None:
    """Find the duration of the playable video stream in seconds."""
    for stream in _playable_video_streams(streams):
        duration = getattr(stream, "duration", None)
        time_base = getattr(stream, "time_base", None)
        if duration is None or time_base is None:
            continue
        seconds = float(duration * time_base)
        if seconds > 0:
            return seconds
    return None


def _container_duration(container, time_base: int) -> float | None:
    duration = _positive_stream_duration(container.streams.video)
    if duration is not None:
        return duration
    if container.duration is not None and container.duration > 0:
        return float(container.duration / time_base)
    return None


def probe_video_duration(path: Path, should_cancel=None) -> float:
    """Read playback duration within the media probe budget, without counting frames."""
    import av

    with BoundedInput(path, should_cancel) as source:
        with av.open(source, options=dict(PROBE_OPTIONS), timeout=5.0) as container:
            duration = _container_duration(container, av.time_base)
        source.check()
    if duration is None:
        raise ValueError("Duration metadata unavailable")
    return duration


def prepare_playable_video_stream(
    video_path: str, output_dir: Path, *, should_cancel: Callable[[], bool] | None = None,
) -> str:
    """Create a video-only source when cover art precedes the real video stream."""
    try:
        import av

        with av.open(video_path) as container:
            streams = list(container.streams.video)
            selected_index = _playable_video_stream_index(streams)
            first_index = int(getattr(streams[0], "index")) if streams else None
    except (ImportError, OSError, ValueError):
        return video_path

    if selected_index is None or selected_index == first_index:
        return video_path

    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_path = output_dir / "playable-video.mkv"
    if normalized_path.is_file() and normalized_path.stat().st_size > 0:
        return str(normalized_path)

    ffmpeg = resolve_ffmpeg()
    run_command(
        [
            ffmpeg,
            "-i",
            video_path,
            "-map",
            f"0:{selected_index}",
            "-c",
            "copy",
            "-an",
            str(normalized_path),
            "-y",
        ],
        should_cancel=should_cancel,
    )
    return str(normalized_path)


def get_video_duration(video_path: str) -> float:
    """Return video duration in seconds."""
    try:
        import av

        with av.open(video_path) as container:
            duration = _container_duration(container, av.time_base)
        if duration is not None:
            return duration
    except (ImportError, OSError, ValueError):
        pass

    import imageio_ffmpeg

    _frames, seconds = imageio_ffmpeg.count_frames_and_secs(video_path)
    return float(seconds)


def get_video_frame_interval(video_path: str, duration_seconds: float | None = None) -> float:
    """Return the source video's average frame duration in seconds."""
    # imageio-ffmpeg's frame counter targets the first video stream. Some MP4
    # files put an attached cover image before the real video, which reports
    # zero frames and would otherwise fail the entire scan.
    try:
        import av

        with av.open(video_path) as container:
            frame_rate = _positive_stream_frame_rate(container.streams.video)
        if frame_rate is not None:
            return 1.0 / frame_rate
    except (ImportError, OSError, ValueError):
        pass

    import imageio_ffmpeg

    frame_count, measured_duration = imageio_ffmpeg.count_frames_and_secs(video_path)
    duration = duration_seconds if duration_seconds is not None else measured_duration
    if frame_count <= 0 or duration <= 0:
        raise ValueError(f"Could not determine a positive frame rate for {video_path}")
    return float(duration) / float(frame_count)


def extract_frames(
    video_path: str,
    output_dir: Path,
    interval_seconds: float = 1.0,
    should_cancel: Callable[[], bool] | None = None,
    duration_seconds: float | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> list[tuple[Path, float]]:
    """
    Extract frames at regular intervals.

    Returns list of (frame_path, timestamp_seconds) tuples.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg()

    # fps filter: 1 frame every N seconds
    fps = 1.0 / interval_seconds
    pattern = str(output_dir / "frame_%06d.jpg")
    duration = duration_seconds if duration_seconds is not None else get_video_duration(video_path)

    def on_ffmpeg_output(line: str) -> None:
        if not progress_callback or not line.startswith("out_time_us="):
            return
        try:
            processed_seconds = int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return
        fraction = min(max(processed_seconds / max(duration, 0.001), 0.0), 1.0)
        progress_callback(
            fraction,
            f"Extracting visual frames ({processed_seconds:.0f}s/{duration:.0f}s)",
        )

    command = [
        ffmpeg,
        "-i",
        video_path,
        "-vf",
        f"fps={fps},{extracted_frame_scale_filter()}",
        "-pix_fmt",
        "yuvj420p",
        "-q:v",
        "5",
        "-progress",
        "pipe:2",
        "-nostats",
        pattern,
        "-y",
    ]

    run_ffmpeg_with_optional_macos_hwaccel(
        command,
        should_cancel=should_cancel,
        output_callback=on_ffmpeg_output,
    )

    frames = sorted(output_dir.glob("frame_*.jpg"))
    results: list[tuple[Path, float]] = []
    for i, frame_path in enumerate(frames):
        timestamp = i * interval_seconds
        if timestamp <= duration:
            results.append((frame_path, timestamp))

    return results
