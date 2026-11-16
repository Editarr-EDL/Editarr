"""Identify the build policy embedded in a distributable runtime."""

import sys
from pathlib import Path


def is_macos_release() -> bool:
    """Return whether the frozen app was built with the macOS release policy."""
    root = getattr(sys, "_MEIPASS", None)
    return (
        sys.platform == "darwin"
        and getattr(sys, "frozen", False)
        and isinstance(root, str)
        and (Path(root) / "macos-release.marker").is_file()
    )
