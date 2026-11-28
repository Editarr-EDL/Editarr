"""Kernel ownership for an isolated helper's durable checkpoint writes."""

import hashlib
import os
from contextlib import contextmanager, suppress
from pathlib import Path

from .maintenance import _lock, _open_lock
from .runtime_paths import ensure_user_private_directory
from .worker_quit import ScanReservationLost


def _lease_path(data_dir: Path, scan_id: str, token: str) -> Path:
    identity = hashlib.sha256(f"{scan_id}\0{token}".encode()).hexdigest()
    # Per-scan checkpoint cleanup may remove an entire result directory. Keep
    # the lock inode separate so a controller can still see the live writer.
    return data_dir / "scan-checkpoints" / "writer-leases" / f"{identity}.lock"


@contextmanager
def checkpoint_writer_lease(data_dir: Path, scan_id: str, token: str):
    """Hold this reservation's lease until the helper finishes writing or exits."""
    path = _lease_path(data_dir, scan_id, token)
    ensure_user_private_directory(path.parent)
    fd = _open_lock(path)
    acquired = False
    try:
        acquired = _lock(fd, exclusive=True, blocking=False)
        if not acquired:
            raise ScanReservationLost("A checkpoint writer is already using this reservation")
        yield
    finally:
        os.close(fd)
        # A killed process leaves an unlocked file. Cooperative exits remove it.
        if acquired:
            with suppress(OSError):
                path.unlink()


def checkpoint_writer_may_be_running(data_dir: Path, scan_id: str, token: str | None) -> bool:
    """Test liveness without creating a lock file or trusting a recycled PID."""
    if token is None:
        return False
    try:
        fd = os.open(_lease_path(data_dir, scan_id, token), os.O_RDWR)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        return not _lock(fd, exclusive=True, blocking=False)
    finally:
        os.close(fd)
