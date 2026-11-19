"""Scene detection via PySceneDetect for speed-up mode."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scanner.pipeline.frames import extracted_frame_scale_filter
from scanner.pipeline.processes import (
    ScanCancelledError,
    run_ffmpeg_with_optional_macos_hwaccel,
)
from scanner.runtime.distribution import has_source_built_media
from scanner.runtime.runtime_support import resolve_ffmpeg
from scanner.settings.config import settings

logger = logging.getLogger(__name__)

# Absorbs floating-point and container timestamp rounding (e.g. Matroska's 1 ms
# ticks) so a frame exactly on a sample time is not skipped for the next one.
_SAMPLE_TIME_TOLERANCE_SECONDS = 0.001


@dataclass
class Scene:
    start_seconds: float
    end_seconds: float
    index: int


def detect_scenes(
    video_path: str,
    threshold: float | None = None,
    duration_seconds: float | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[Scene]:
    """
    Detect scene boundaries using PySceneDetect's ContentDetector.

    ContentDetector finds fast cuts by comparing HSV channel changes
    between consecutive frames.
    """
    threshold = threshold if threshold is not None else settings.scene_detection_threshold
    from scenedetect import ContentDetector, SceneManager, open_video

    logger.info("Detecting scenes (threshold=%.1f)...", threshold)
    # The release's OpenCV is built without video codecs; PyAV shares our FFmpeg.
    video = (
        open_video(video_path, backend="pyav")
        if has_source_built_media()
        else open_video(video_path)
    )
    detected_duration = duration_seconds
    if detected_duration is None and video.duration is not None:
        detected_duration = video.duration.get_seconds()

    last_reported_seconds = -1.0

    class ReportingContentDetector(ContentDetector):
        def process_frame(self, timecode, frame_img):
            """Inspect a decoded frame and update scene detection progress."""
            nonlocal last_reported_seconds
            if should_cancel and should_cancel():
                raise ScanCancelledError("Scan cancelled")

            cuts = super().process_frame(timecode, frame_img)
            processed_seconds = timecode.get_seconds()
            if progress_callback and (
                processed_seconds - last_reported_seconds >= 1.0
                or detected_duration is None
                or processed_seconds >= detected_duration
            ):
                last_reported_seconds = processed_seconds
                if detected_duration is None:
                    progress_callback(0.0, "Detecting scene boundaries")
                else:
                    progress_callback(
                        min(processed_seconds / max(detected_duration, 0.001), 1.0),
                        (
                            "Detecting scene boundaries "
                            f"({processed_seconds:.0f}s/{detected_duration:.0f}s)"
                        ),
                    )
            return cuts

    if progress_callback:
        progress_callback(0.0, "Detecting scene boundaries")
    scene_manager = SceneManager()
    scene_manager.add_detector(ReportingContentDetector(threshold=threshold))
    scene_manager.detect_scenes(video=video)
    scene_list = scene_manager.get_scene_list(start_in_scene=True)

    if not scene_list:
        from scanner.pipeline.frames import get_video_duration

        duration = (
            detected_duration if detected_duration is not None else get_video_duration(video_path)
        )
        logger.info("No scene cuts found — treating entire video as one scene")
        return [Scene(start_seconds=0.0, end_seconds=duration, index=0)]

    scenes = [
        Scene(
            start_seconds=scene_start.get_seconds(),
            end_seconds=scene_end.get_seconds(),
            index=i,
        )
        for i, (scene_start, scene_end) in enumerate(scene_list)
    ]

    logger.info("Found %d scenes", len(scenes))
    return scenes


def interval_sample_offset(interval_seconds: float, duration_seconds: float) -> float:
    """Return the scene-relative time of the first centered interval sample.

    Samples sit at the middle of each ``interval_seconds`` bin, and a scene shorter
    than one interval is sampled once at its midpoint. Centering also keeps samples
    away from cut frames, which can still show a transition.
    """
    return min(interval_seconds, duration_seconds) / 2


def interval_sample_filter(interval_seconds: float, offset_seconds: float) -> str:
    """Return an FFmpeg filter keeping the first frame at or after each sample time.

    Frame ``k`` is the first decoded frame whose scene-relative time is at least
    ``offset_seconds + k * interval_seconds`` (less a 1 ms rounding tolerance), so
    it lies within one source frame of that time. FFmpeg's ``fps`` filter is
    unsuitable: it rounds to the nearest output slot, so its images do not match
    their nominal times.
    """
    if interval_seconds <= 0:
        raise ValueError("Frame sample interval must be greater than zero")
    first_sample = offset_seconds - _SAMPLE_TIME_TOLERANCE_SECONDS
    return f"select=gte(t\\,{first_sample!r}+selected_n*{interval_seconds!r})"


def sample_scene_frames(
    video_path: str,
    scene: Scene,
    output_dir: Path,
    interval_seconds: float | None,
    timestamp_interval_seconds: float | None = None,
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> list[tuple[Path, float]]:
    """Extract every source frame, or centered interval samples, from one scene.

    Returns ``(path, timestamp)`` pairs inside ``[scene.start, scene.end)``. With
    ``interval_seconds`` the k-th frame is the first at or after
    ``scene.start + offset + k * interval_seconds`` (see
    :func:`interval_sample_offset`) and is stamped with that time. Otherwise every
    source frame is extracted and stamped every ``timestamp_interval_seconds``
    (normally the source frame interval).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg()

    duration = scene.end_seconds - scene.start_seconds
    if duration <= 0:
        return []

    pattern = str(output_dir / f"scene_{scene.index:04d}_%06d.jpg")
    filters = [extracted_frame_scale_filter()]
    frame_rate_options: list[str] = []
    sample_offset = 0.0
    if interval_seconds is not None:
        sample_offset = interval_sample_offset(interval_seconds, duration)
        filters.insert(0, interval_sample_filter(interval_seconds, sample_offset))
        frame_rate_options = ["-fps_mode", "vfr"]

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
            f"Extracting scene frames ({processed_seconds:.0f}s/{duration:.0f}s)",
        )

    command = [
        ffmpeg,
        "-ss",
        str(scene.start_seconds),
        "-i",
        video_path,
        "-t",
        str(duration),
        "-vf",
        ",".join(filters),
        *frame_rate_options,
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

    frames = sorted(output_dir.glob(f"scene_{scene.index:04d}_*.jpg"))
    if not frames:
        # A scene spanning about one source frame can have no frame at or after
        # its midpoint. Seeking to the start is the robust fallback: it returns
        # the first frame at or after the start, while a later seek could land
        # in the next scene.
        fallback_path = output_dir / f"scene_{scene.index:04d}_fallback.jpg"
        fallback_command = [
            ffmpeg,
            "-ss",
            str(scene.start_seconds),
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-vf",
            extracted_frame_scale_filter(),
            "-pix_fmt",
            "yuvj420p",
            "-q:v",
            "5",
            str(fallback_path),
            "-y",
        ]
        run_ffmpeg_with_optional_macos_hwaccel(
            fallback_command,
            should_cancel=should_cancel,
        )
        if fallback_path.is_file():
            return [(fallback_path, scene.start_seconds)]
    if not frames:
        return []
    results: list[tuple[Path, float]] = []
    if interval_seconds is not None:
        first_timestamp = scene.start_seconds + sample_offset
        timestamp_interval_seconds = interval_seconds
    else:
        first_timestamp = scene.start_seconds
        if timestamp_interval_seconds is None:
            timestamp_interval_seconds = duration / len(frames)
    for i, frame_path in enumerate(frames):
        timestamp = first_timestamp + (i * timestamp_interval_seconds)
        if timestamp < scene.end_seconds:
            results.append((frame_path, timestamp))

    return results
