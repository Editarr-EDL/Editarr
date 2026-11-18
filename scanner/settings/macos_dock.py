"""Persist the packaged macOS launcher's Dock visibility preference."""

import os
import sys
from pathlib import Path

from scanner.runtime.durability import atomic_write_bytes
from scanner.runtime.runtime_paths import ensure_user_private_directory

_FILENAME = "macos-show-in-dock"


def macos_launcher_available() -> bool:
    """Only the native Mac shell supplies this capability to its runtime."""
    return sys.platform == "darwin" and os.environ.get("EDITARR_MACOS_APP") == "1"


def read_show_in_dock(data_dir: Path) -> bool:
    """Read the saved preference, defaulting to a visible Dock icon."""
    try:
        value = (data_dir / _FILENAME).read_bytes()
    except FileNotFoundError:
        return True
    if value not in {b"0\n", b"1\n"}:
        raise ValueError("Invalid Dock icon preference")
    return value == b"1\n"


def write_show_in_dock(data_dir: Path, enabled: bool) -> None:
    """Replace the preference atomically so the launcher can read it during a save."""
    directory = ensure_user_private_directory(data_dir)
    atomic_write_bytes(
        directory / _FILENAME, b"1\n" if enabled else b"0\n", prefix=".macos-dock-"
    )
