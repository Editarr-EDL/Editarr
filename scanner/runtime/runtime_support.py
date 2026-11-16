"""Early cache and immutable-helper support for native Editarr runtimes.

This module intentionally depends only on the standard library and RuntimePaths.  It
can run before optional model/helper packages are imported, and never writes to the
application resources directory.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from scanner.runtime.runtime_paths import RuntimePaths, get_runtime_paths

_MODEL_CACHE_ENVIRONMENT = {
    "HF_HOME": ("huggingface",),
    "HUGGINGFACE_HUB_CACHE": ("huggingface", "hub"),
    "TRANSFORMERS_CACHE": ("huggingface", "transformers"),
    "TORCH_HOME": ("torch",),
    "CT2_CACHE_DIR": ("ctranslate2",),
}


def configure_model_cache_environment(paths: RuntimePaths | None = None) -> dict[str, Path]:
    """Set unset model-library cache variables beneath ``paths.model_cache``.

    Operators may deliberately provide any of these variables, so existing nonempty
    values always win.  The function only configures process environment; consumers
    create their individual cache directories when they actually download or compile.
    """

    runtime_paths = paths or get_runtime_paths()
    model_cache = Path(runtime_paths.model_cache)
    resolved: dict[str, Path] = {}
    for name, suffix in _MODEL_CACHE_ENVIRONMENT.items():
        explicit = os.environ.get(name)
        if explicit and explicit.strip():
            resolved[name] = Path(explicit)
            continue
        destination = model_cache.joinpath(*suffix)
        os.environ[name] = str(destination)
        resolved[name] = destination
    return resolved


def _helper_resource_path(name: str, paths: RuntimePaths) -> Path | None:
    """Return a bundled immutable helper when a future frozen build supplies one."""

    executable_name = f"{name}.exe" if os.name == "nt" else name
    candidate = Path(paths.resources_dir) / "helpers" / executable_name
    return candidate if candidate.is_file() else None


def resolve_ffmpeg(paths: RuntimePaths | None = None) -> str:
    """Resolve FFmpeg without placing binaries or downloads in application resources."""

    explicit = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if explicit and explicit.strip():
        return explicit
    runtime_paths = paths or get_runtime_paths()
    bundled = _helper_resource_path("ffmpeg", runtime_paths)
    if bundled is not None:
        return str(bundled)

    # Source installs retain imageio-ffmpeg's existing helper discovery behavior.
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def resolve_ffprobe(paths: RuntimePaths | None = None) -> str | None:
    """Resolve ffprobe beside a selected/bundled FFmpeg or from the source host PATH."""

    runtime_paths = paths or get_runtime_paths()
    bundled = _helper_resource_path("ffprobe", runtime_paths)
    if bundled is not None:
        return str(bundled)
    ffmpeg = Path(resolve_ffmpeg(runtime_paths))
    sibling = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if sibling.is_file():
        return str(sibling)
    return shutil.which("ffprobe")
