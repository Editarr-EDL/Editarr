"""Persist the packaged macOS launcher's Dock visibility preference."""

import os
import sys
import tempfile
from pathlib import Path

from scanner.runtime.runtime_paths import ensure_user_private_directory

_FILENAME = "macos-show-in-dock"


def macos_launcher_available() -> bool:
    """Only the native Mac shell supplies this capability to its runtime."""
    return sys.platform == "darwin" and os.environ.get("EDITARR_MACOS_APP") == "1"


def read_show_in_dock(data_dir: Path) -> bool:
    """Read the saved preference, defaulting to menu-bar-only operation."""
    try:
        value = (data_dir / _FILENAME).read_bytes()
    except FileNotFoundError:
        return False
    if value not in {b"0\n", b"1\n"}:
        raise ValueError("Invalid Dock icon preference")
    return value == b"1\n"


def write_show_in_dock(data_dir: Path, enabled: bool) -> None:
    """Replace the preference atomically so the launcher can read it during a save."""
    directory = ensure_user_private_directory(data_dir)
    descriptor, temporary = tempfile.mkstemp(prefix=".macos-dock-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(b"1\n" if enabled else b"0\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / _FILENAME)
    finally:
        Path(temporary).unlink(missing_ok=True)
