"""Cross-process database admission and explicit maintenance coordination."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from scanner.runtime.runtime_paths import ensure_user_private_directory

_owner_thread = threading.local()


class MaintenanceUnavailable(RuntimeError):
    """Database access is paused or exclusive maintenance cannot start."""


def _lock(fd: int, *, exclusive: bool, blocking: bool) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            mode = (
                (msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK)
                if exclusive
                else (msvcrt.LK_RLCK if blocking else msvcrt.LK_NBRLCK)
            )
            msvcrt.locking(fd, mode, 1)
        else:
            import fcntl

            fcntl.flock(
                fd,
                (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
                | (0 if blocking else fcntl.LOCK_NB),
            )
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _open_lock(path: Path) -> int:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    if os.fstat(fd).st_size == 0:
        os.write(fd, b"\0")
        os.fsync(fd)
    return fd


class DatabaseMaintenance:
    """Gate connections and serialize maintenance for one private data directory."""

    def __init__(self, data_dir: Path):
        self.directory = ensure_user_private_directory(Path(data_dir) / "maintenance")
        self.owner_path = self.directory / "owner.lock"
        self.users_path = self.directory / "users.lock"
        self.pending_path = self.directory / "pending"

    def _pending(self) -> bool:
        if not self.pending_path.exists():
            return False
        owner = _open_lock(self.owner_path)
        try:
            if _lock(owner, exclusive=True, blocking=False):
                try:
                    self.pending_path.unlink(missing_ok=True)
                finally:
                    _unlock(owner)
                return False
            return True
        finally:
            os.close(owner)

    def checkout(self) -> int | None:
        """Hold a shared kernel lock for a checked-out DB connection."""
        if getattr(_owner_thread, "directory", None) == self.directory:
            return None
        if (self.directory.parent / "restore-activation.json").exists():
            raise MaintenanceUnavailable("Restore recovery is required before database access")
        if self._pending():
            raise MaintenanceUnavailable("Database maintenance is pending; retry after it finishes")
        fd = _open_lock(self.users_path)
        try:
            if not _lock(fd, exclusive=False, blocking=False):
                raise MaintenanceUnavailable("Database maintenance is active; retry later")
            if self._pending() or (self.directory.parent / "restore-activation.json").exists():
                raise MaintenanceUnavailable("Database maintenance is pending; retry later")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def checkin(self, fd: int) -> None:
        _unlock(fd)
        os.close(fd)

    def requested(self) -> bool:
        """Signal an active worker to checkpoint and close without cancelling its scan."""
        return self._pending()

    def owns_exclusive(self) -> bool:
        """Check whether this thread already holds this data directory's exclusive lease."""
        return getattr(_owner_thread, "directory", None) == self.directory

    @contextmanager
    def worker_lease(self):
        """Keep the entire dedicated worker alive within the shared-user barrier."""
        fd = self.checkout()
        try:
            yield
        finally:
            if fd is not None:
                self.checkin(fd)

    @contextmanager
    def exclusive(self, *, timeout: float = 10.0):
        """Stop new checkouts, wait for active users, and hold exclusive access."""
        owner = _open_lock(self.owner_path)
        users = None
        acquired = False
        try:
            if not _lock(owner, exclusive=True, blocking=False):
                raise MaintenanceUnavailable("Another database maintenance owner is active")
            acquired = True
            self.pending_path.touch(mode=0o600, exist_ok=True)
            users = _open_lock(self.users_path)
            deadline = time.monotonic() + timeout
            while not _lock(users, exclusive=True, blocking=False):
                if time.monotonic() >= deadline:
                    raise MaintenanceUnavailable(
                        "Timed out waiting for database users to finish; "
                        "original data was not changed"
                    )
                time.sleep(0.05)
            _owner_thread.directory = self.directory
            try:
                yield
            finally:
                del _owner_thread.directory
        finally:
            if users is not None:
                os.close(users)
            if acquired:
                self.pending_path.unlink(missing_ok=True)
            os.close(owner)
