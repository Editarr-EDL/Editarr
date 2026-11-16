"""No-weight import and helper smoke used only to verify a frozen Editarr runtime."""

from __future__ import annotations

import importlib
import platform
import subprocess

from .runtime_support import resolve_ffmpeg, resolve_ffprobe

_BASE_IMPORTS = (
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


def run_runtime_smoke() -> int:
    """Import lazy runtime families and execute bundled media helpers without weights."""

    for name in _BASE_IMPORTS:
        importlib.import_module(name)
    if platform.system() == "Darwin":
        for name in _MACOS_IMPORTS:
            importlib.import_module(name)
    ffmpeg = resolve_ffmpeg()
    imageio_ffmpeg = importlib.import_module("imageio_ffmpeg")
    if imageio_ffmpeg.get_ffmpeg_exe() != ffmpeg:
        raise RuntimeError("imageio-ffmpeg does not use the bundled FFmpeg helper")
    _run_helper(ffmpeg, "ffmpeg")
    ffprobe = resolve_ffprobe()
    if ffprobe is None:
        raise RuntimeError("Bundled ffprobe is unavailable")
    _run_helper(ffprobe, "ffprobe")
    print("Editarr frozen runtime smoke passed")
    return 0
