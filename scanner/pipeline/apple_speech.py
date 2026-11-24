"""Apply Editarr's locale and transcript conventions to native Apple Speech."""

from __future__ import annotations

import json
import platform
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scanner.modeling.model_acquisition_authority import require_acquisition
from scanner.runtime.apple_speech.helper import MINIMUM_MACOS, resolve_helper
from scanner.settings.config import settings
from scanner.settings.model_runtime import version_at_least

from .processes import ScanCancelledError, run_command

_LOCALE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,3}")
_SPEECH_ASSETS_MISSING_EXIT = 2


def speech_locale(audio_language: str | None = None) -> str:
    """Choose a locale from the deployment override or the selected track's language."""
    value = (settings.apple_speech_locale or audio_language or "").strip().lower()
    value = value.replace("_", "-")
    if value.split("-")[0] in {"", "und", "unk", "mul", "zxx"} or not _LOCALE.fullmatch(value):
        return "en-us"
    return value


def transcript_revision(locale: str) -> str:
    """Version Apple's transcript format independently of Whisper checkpoint settings."""
    return f"v1.{locale}"


def _transcribe_with_assets(
    command: list[str], locale: str, should_cancel: Callable[[], bool] | None
) -> None:
    """Try installed assets first and authorize any separate installation."""
    try:
        run_command(command, should_cancel=should_cancel)
        return
    except subprocess.CalledProcessError as exc:
        if exc.returncode != _SPEECH_ASSETS_MISSING_EXIT:
            raise

    def cancel_install() -> bool:
        if should_cancel and should_cancel():
            return True
        require_acquisition(download=True)
        return False

    if cancel_install():
        raise ScanCancelledError("Scan cancelled")
    run_command([command[0], "--install-assets", locale], should_cancel=cancel_install)
    run_command(command, should_cancel=should_cancel)


def transcribe(
    audio_path: str,
    *,
    locale: str,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """Transcribe a file on an Apple Silicon Mac and return native timed segments."""
    if platform.system() != "Darwin" or platform.machine() not in {"arm64", "aarch64"}:
        raise RuntimeError("Apple Speech requires an Apple Silicon Mac.")
    if not version_at_least(platform.mac_ver()[0], (MINIMUM_MACOS,)):
        raise RuntimeError(f"Apple Speech requires macOS {MINIMUM_MACOS} or later.")
    if should_cancel and should_cancel():
        raise ScanCancelledError("Scan cancelled")
    helper = resolve_helper(settings.cache_dir, should_cancel=should_cancel)
    with TemporaryDirectory(prefix="editarr-apple-speech-") as directory:
        output = Path(directory) / "transcript.json"
        try:
            _transcribe_with_assets(
                [str(helper), locale, str(Path(audio_path).resolve()), str(output)],
                locale,
                should_cancel,
            )
        except subprocess.CalledProcessError as exc:
            error = (exc.stderr or "").strip() or f"helper exited with code {exc.returncode}"
            raise RuntimeError(f"Apple Speech transcription failed: {error}") from exc
        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")
        try:
            segments = json.loads(output.read_bytes())
        except (OSError, ValueError) as exc:
            raise RuntimeError("Apple Speech returned an unreadable transcript.") from exc
    if not isinstance(segments, list) or not all(isinstance(segment, dict) for segment in segments):
        raise RuntimeError("Apple Speech returned an invalid transcript.")
    return segments
