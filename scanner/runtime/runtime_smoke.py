"""Offline import, detector, and helper checks for a frozen Editarr runtime."""

from __future__ import annotations

import importlib
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path

from .distribution import has_source_built_media
from .model_smoke import (
    FRAME_ENVIRONMENT,
    MODEL_ENVIRONMENT,
    d1_unsupported_reason,
    run_model_probe,
)
from .remote_code_smoke import smoke_remote_code
from .runtime_support import resolve_ffmpeg, resolve_ffprobe

_BASE_IMPORTS = (
    # Settings point the Hugging Face caches at the model cache, as in the app, before
    # huggingface_hub reads them at import.
    "scanner.settings.config",
    "certifi",
    "cryptography",
    "fastapi",
    "onnxruntime",
    "sqlalchemy",
    "torch",
    "transformers",
    "uvicorn",
    "scanner.api",
    "scanner.mcf",
    "scanner.model_acquisition",
    "scanner.pipeline.audio",
    "scanner.pipeline.image_safety_gate",
    "scanner.pipeline.qwen_summary",
    "scanner.pipeline.siglip2_moderation",
    "scanner.pipeline.shieldstral",
    "scanner.pipeline.qwen_detector",
    "scanner.pipeline.d1_detector",
)
_MACOS_IMPORTS = ("mlx", "mlx_vlm", "mlx_whisper")


def _run_helper(path: str, label: str) -> None:
    """Run one packaged runtime helper and return its smoke-check result."""
    try:
        completed = subprocess.run(
            [path, "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Bundled {label} did not execute: {error}") from error
    if completed.returncode != 0:
        raise RuntimeError(f"Bundled {label} failed its version probe")


def _smoke_release_media(ffmpeg: str) -> None:
    """Prove the release helpers can encode previews and PyAV can read their output."""
    from nudenet import NudeDetector

    from scanner.pipeline.scenes import detect_scenes

    from .mute_preview import render_mute_preview, render_video_preview

    with tempfile.TemporaryDirectory(prefix="editarr-media-smoke-") as temp:
        video = Path(temp) / "example.mp4"
        subprocess.run(
            [
                ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=size=160x90:rate=24",
                "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2",
                "-c:v", "libx264", "-c:a", "aac", str(video),
            ],
            check=True,
            timeout=30,
        )
        if not detect_scenes(str(video)):
            raise RuntimeError("Packaged PyAV failed to open and decode a video")
        if not render_mute_preview(video, 0.25, 0.75):
            raise RuntimeError("Packaged FFmpeg did not produce an MP3 preview")
        if not render_video_preview(video, 0.25, 0.75):
            raise RuntimeError("Packaged FFmpeg did not produce an MP4 preview")
        image = Path(temp) / "frame.jpg"
        subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(video), "-frames:v", "1", str(image)],
            check=True,
            timeout=30,
        )
        if not isinstance(NudeDetector().detect(str(image)), list):
            raise RuntimeError("Packaged NudeNet did not return a detection list")


def _smoke_d1_remote_code() -> None:
    """Load d1-3B's remote-code path where d1-3B can run, otherwise confirm it is gated off."""
    from scanner.pipeline import d1_detector
    from scanner.settings.capabilities import capability_for

    unsupported = d1_unsupported_reason()
    if unsupported is None:
        smoke_remote_code(d1_detector.REMOTE_CODE_IMPORTS, d1_detector.RESOLVED_MODULES)
        return
    capability = capability_for("d1-3b")
    if (capability.availability, capability.reason) != (
        "unavailable",
        "unsupported_dependency_version",
    ):
        raise RuntimeError(
            f"d1-3B cannot run with {unsupported} but its capability reports "
            f"{capability.availability} ({capability.reason})"
        )
    print(f"d1-3B remote-code probe skipped: {unsupported} (d1-3B reported unavailable)")


def run_runtime_smoke() -> int:
    """Exercise lazy imports, remote model code, the profanity detector, and media helpers.

    With ``EDITARR_FROZEN_RUNTIME_SMOKE_MODEL`` and ``EDITARR_FROZEN_RUNTIME_SMOKE_FRAME``
    set, also run that frame through the already-cached model and print the result.
    """

    for name in _BASE_IMPORTS:
        importlib.import_module(name)
    if platform.system() == "Darwin":
        for name in _MACOS_IMPORTS:
            importlib.import_module(name)
    _smoke_d1_remote_code()

    from scanner.pipeline.audio_filters import _load_alt_profanity_check_detector

    detector = _load_alt_profanity_check_detector()
    if detector("Good morning"):
        raise RuntimeError("Bundled alt-profanity-check failed its prediction probe")
    ffmpeg = resolve_ffmpeg()
    imageio_ffmpeg = importlib.import_module("imageio_ffmpeg")
    if imageio_ffmpeg.get_ffmpeg_exe() != ffmpeg:
        raise RuntimeError("imageio-ffmpeg does not use the bundled FFmpeg helper")
    _run_helper(ffmpeg, "ffmpeg")
    ffprobe = resolve_ffprobe()
    if ffprobe is None:
        raise RuntimeError("Bundled ffprobe is unavailable")
    _run_helper(ffprobe, "ffprobe")
    if has_source_built_media():
        _smoke_release_media(ffmpeg)
    model = os.environ.get(MODEL_ENVIRONMENT)
    if model:
        frame = Path(os.environ.get(FRAME_ENVIRONMENT, ""))
        print(json.dumps(run_model_probe(model, frame), sort_keys=True))
    print("Editarr frozen runtime smoke passed")
    return 0
