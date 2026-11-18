"""Filesystem durability operations shared by exports and recovery snapshots."""

import os
import tempfile
from pathlib import Path


def sync_directory(path: Path) -> None:
    """Persist directory entries on platforms that support directory fsync."""
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    prefix: str,
    suffix: str = "",
    mode: int | None = None,
    sync_dir: bool = False,
) -> None:
    """Durably replace ``path`` with ``data`` through a private sibling temporary file.

    The temporary file is created by ``mkstemp`` (owner-only), fsynced, optionally
    chmodded to ``mode``, then renamed over ``path``. ``sync_dir`` also fsyncs the
    parent directory and propagates any failure; callers that tolerate directory sync
    failures should call ``sync_directory`` themselves.
    """
    directory = path.parent
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, path)
        if sync_dir:
            sync_directory(directory)
    finally:
        temporary.unlink(missing_ok=True)
