"""Render short browser-playable previews of individual scan findings."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from scanner.pipeline.frames import playable_video_stream_index
from scanner.runtime.runtime_support import resolve_ffmpeg

MINIMUM_MUTE_PREVIEW_SECONDS = 1.0


def render_mute_preview(media_path: str | Path, start_seconds: float, end_seconds: float) -> bytes:
    """Render one mute range as a browser-playable MP3, at least one second long."""
    if end_seconds <= start_seconds:
        raise ValueError("The mute filter does not have a valid time range")

    muted_duration_seconds = end_seconds - start_seconds
    preview_start_seconds = start_seconds
    preview_end_seconds = end_seconds
    if muted_duration_seconds < MINIMUM_MUTE_PREVIEW_SECONDS:
        padding_seconds = (MINIMUM_MUTE_PREVIEW_SECONDS - muted_duration_seconds) / 2
        preview_start_seconds = max(0, start_seconds - padding_seconds)
        preview_end_seconds = end_seconds + padding_seconds
        if preview_end_seconds - preview_start_seconds < MINIMUM_MUTE_PREVIEW_SECONDS:
            preview_end_seconds = preview_start_seconds + MINIMUM_MUTE_PREVIEW_SECONDS
    duration_seconds = preview_end_seconds - preview_start_seconds
    command = [
        resolve_ffmpeg(),
        "-v",
        "error",
        "-nostdin",
        # Input seeking means ffmpeg reads only the short audio clip instead
        # of decoding the whole feature-length media file.
        "-ss",
        f"{preview_start_seconds:.3f}",
        "-i",
        str(media_path),
        "-t",
        f"{duration_seconds:.3f}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        "-f",
        "mp3",
        "pipe:1",
    ]

    if sys.platform == "darwin":
        input_index = command.index("-i")
        hw_command = [
            *command[:input_index],
            "-hwaccel",
            "videotoolbox",
            *command[input_index:],
        ]
        try:
            result = subprocess.run(hw_command, check=True, capture_output=True)
            return result.stdout
        except subprocess.CalledProcessError:
            pass

    try:
        result = subprocess.run(command, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Unable to render mute preview: {message or exc}") from exc
    return result.stdout


def render_video_preview(media_path: str | Path, start_seconds: float, end_seconds: float) -> bytes:
    """Render one exact skip range as a browser-playable MP4 preview."""
    if end_seconds <= start_seconds:
        raise ValueError("The video filter does not have a valid time range")

    duration_seconds = end_seconds - start_seconds
    stream_index = playable_video_stream_index(str(media_path))
    video_stream = f"0:{stream_index}" if stream_index is not None else "0:v:0"
    # A normal fast-start MP4 carries an accurate duration and index. In
    # contrast, fragmented MP4 written to stdout can render as a one-second,
    # blank clip in Safari even when ffmpeg decoded the complete range.
    with tempfile.NamedTemporaryFile(suffix=".mp4") as output_file:
        command = [
            resolve_ffmpeg(),
            "-v",
            "error",
            "-nostdin",
            "-y",
            # Input seeking means ffmpeg reads only this filter's short clip.
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            str(media_path),
            "-t",
            f"{duration_seconds:.3f}",
            "-map",
            video_stream,
            "-map",
            "0:a:0?",
            # H.264 needs even dimensions, and these codecs are broadly
            # supported by the browsers Editarr targets.
            "-vf",
            "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v",
            "libx264",
            "-tag:v",
            "avc1",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            output_file.name,
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"Unable to render video preview: {message or exc}") from exc
        return Path(output_file.name).read_bytes()
