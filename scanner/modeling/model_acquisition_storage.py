"""Thread and process locks plus integrity helpers for model artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .model_acquisition_manifest import (
    _DOWNLOAD_CHUNK_SIZE,
    AcquisitionCancelled,
    CancelCheck,
)

_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


def _local_lock(path: Path) -> threading.Lock:
    key = str(path.absolute())
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(key, threading.Lock())


@contextmanager
def _cross_process_lock(path: Path, cancelled: CancelCheck) -> Iterator[None]:
    """Hold one lock in threads and processes, while remaining cancellable."""

    local = _local_lock(path)
    while not local.acquire(timeout=0.1):
        if cancelled():
            raise AcquisitionCancelled(path.stem)
    handle = None
    locked = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if cancelled():
                        raise AcquisitionCancelled(path.stem)
                    time.sleep(0.1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if cancelled():
                        raise AcquisitionCancelled(path.stem)
                    time.sleep(0.1)
        yield
    finally:
        if handle is not None:
            try:
                if locked and os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                elif locked:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        local.release()


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _file_integrity(path: Path) -> tuple[int, str, str]:
    """Return size, SHA-256, and Git blob SHA-1 for the file at path."""
    stat = path.stat()
    sha256 = hashlib.sha256()
    git_object_sha1 = hashlib.sha1(f"blob {stat.st_size}\0".encode())
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_DOWNLOAD_CHUNK_SIZE):
            size += len(chunk)
            sha256.update(chunk)
            git_object_sha1.update(chunk)
    return size, sha256.hexdigest(), git_object_sha1.hexdigest()
