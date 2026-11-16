"""Filesystem durability operations shared by exports and recovery snapshots."""

import os
from pathlib import Path


def sync_directory(path: Path) -> None:
    """Persist directory entries on platforms that support directory fsync."""
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
