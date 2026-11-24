"""Prepare target-specific executables for collection into a frozen runtime."""

import shutil
from pathlib import Path

from .runtime_layout import helper_name


def imageio_ffmpeg() -> Path:
    """Find the FFmpeg binary provided by the target wheel."""
    import imageio_ffmpeg

    helper = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    if not helper.is_file():
        raise RuntimeError("imageio-ffmpeg did not resolve a bundled FFmpeg helper")
    return helper


def target_helper(value: str | None, name: str) -> Path:
    """Validate a supplied release toolchain executable before collecting it."""
    if not value:
        raise RuntimeError(
            f"A relocatable {name} helper is required. Pass --{name}-helper from the "
            "target release toolchain; do not use an untracked system PATH dependency."
        )
    supplied = Path(value).expanduser()
    helper = supplied.resolve()
    if supplied.is_symlink() or not helper.is_file():
        raise RuntimeError(f"--{name}-helper must name a regular target helper executable")
    return helper


def stage_helpers(helpers: dict[str, Path], build_root: Path, *, target: str) -> dict[str, Path]:
    """Give collected helpers stable runtime filenames independent of wheel filenames."""
    destination = build_root / "helpers"
    destination.mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}
    for name, source in helpers.items():
        staged_path = destination / helper_name(name, target)
        shutil.copy2(source, staged_path)
        staged_path.chmod(source.stat().st_mode)
        staged[name] = staged_path
    return staged


def build_apple_speech(build_root: Path) -> Path:
    """Compile the native Speech framework adapter with a current macOS SDK."""
    from scanner.runtime.apple_speech.helper import HELPER_NAME, compile_helper

    result = build_root / HELPER_NAME
    compile_helper(result, module_cache=build_root / "swift-modules")
    return result
