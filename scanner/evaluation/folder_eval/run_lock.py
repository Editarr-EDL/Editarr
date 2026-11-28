"""One `run` per model at a time: an exclusive per-model lock in `_results/models`.

The lock is an OS advisory lock (flock, or LockFileEx on Windows) on
`models/.<model id>.lock`, held for the whole run including `--restart`, the
configuration check and every append. The OS drops it when the process exits, so a
crashed run never leaves a stale lock; the holder's pid is stored only for messages.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from scanner.runtime.maintenance import _lock, _unlock

from .paths import Layout, ensure_results_destination, open_regular


class ModelBusy(RuntimeError):
    """Another process is scoring this model."""


def lock_path(layout: Layout, model_id: str):
    return layout.models / f".{model_id}.lock"


def _holder(fd: int) -> str:
    os.lseek(fd, 1, os.SEEK_SET)
    text = os.read(fd, 64).decode("ascii", "replace").strip()
    return f"process {text}" if text else "another process"


@contextmanager
def model_lock(layout: Layout, model_id: str) -> Iterator[None]:
    """Hold `model_id`'s run lock, or raise ModelBusy naming the process that holds it."""
    path = lock_path(layout, model_id)
    ensure_results_destination(path)
    layout.models.mkdir(parents=True, exist_ok=True)
    fd = open_regular(path, os.O_RDWR | os.O_CREAT)  # never a planted link's target
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        if not _lock(fd, exclusive=True, blocking=False):
            raise ModelBusy(
                f"{model_id} is already being scored by {_holder(fd)}; wait for that run to "
                "finish (or stop it) before running or restarting this model."
            )
        try:
            # Byte 0 is the locked byte; the pid after it is only for the message above.
            os.lseek(fd, 1, os.SEEK_SET)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            os.ftruncate(fd, os.lseek(fd, 0, os.SEEK_CUR))
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
