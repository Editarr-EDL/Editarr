"""Discover and build the Apple Speech command-line adapter."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from scanner.pipeline.processes import run_command
from scanner.runtime.runtime_paths import (
    ensure_user_private_directory,
    is_frozen_application,
    resolve_resource_directory,
)

HELPER_NAME = "apple-speech"
SOURCE = Path(__file__).with_name("AppleSpeech.swift")
MINIMUM_MACOS = 26
_XCRUN = ("/usr/bin/xcrun", "--sdk", "macosx")


@lru_cache(maxsize=1)
def runtime_available() -> bool:
    """Check the packaged helper or the source toolchain without building anything."""
    if is_frozen_application():
        helper = bundled_helper()
        return helper.is_file() and os.access(helper, os.X_OK)
    try:
        sdk = subprocess.check_output(
            [*_XCRUN, "--show-sdk-version"], stderr=subprocess.DEVNULL, text=True, timeout=3
        )
    except (OSError, subprocess.SubprocessError):
        return False
    version = re.match(r"\d+", sdk)
    return SOURCE.is_file() and version is not None and int(version[0]) >= MINIMUM_MACOS


def bundled_helper() -> Path:
    """Return the helper path inside a native macOS distribution."""
    return resolve_resource_directory() / "helpers" / HELPER_NAME


def compile_helper(
    destination: Path,
    *,
    module_cache: Path,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    """Compile against the modern Speech SDK while retaining a weak runtime dependency."""
    try:
        run_command(
            [
                *_XCRUN, "swiftc", "-O", "-parse-as-library", "-target", "arm64-apple-macos14.0",
                "-module-cache-path", str(module_cache), str(SOURCE), "-o", str(destination),
            ],
            should_cancel=should_cancel,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Could not build the Apple Speech helper: {exc.stderr}") from exc


def resolve_helper(cache_dir: Path, *, should_cancel: Callable[[], bool] | None) -> Path:
    """Use the packaged binary or atomically cache a source build outside the checkout."""
    if not runtime_available():
        raise RuntimeError(
            "Apple Speech requires the Editarr macOS app or Command Line Tools with a macOS 26 SDK."
        )
    if is_frozen_application():
        return bundled_helper()
    directory = ensure_user_private_directory(cache_dir / "helpers" / HELPER_NAME)
    revision = sha256(SOURCE.read_bytes()).hexdigest()[:16]
    destination = directory / f"{HELPER_NAME}-{revision}"
    if not os.access(destination, os.X_OK):
        with TemporaryDirectory(dir=directory) as temporary:
            build = Path(temporary)
            binary = build / HELPER_NAME
            compile_helper(binary, module_cache=build / "modules", should_cancel=should_cancel)
            os.replace(binary, destination)
    return destination
