"""Private-file access and interprocess locking for community identities."""

import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from scanner.runtime.maintenance import _lock, _unlock
from scanner.runtime.runtime_paths import ensure_user_private_directory

# Upgrades and restores place one identity here; it is then moved into its home.
DIRECTORY = Path("secrets") / "community"
HOMES = Path("secrets") / "community-identities"
LOCK_FILE = "identity.lock"
_THREAD_LOCK = RLock()


class IdentityUnavailable(RuntimeError):
    """Local identity needs backup recovery or explicit enrollment repair."""

    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class IdentityHome:
    """One account's private identity directory, named by its `community_identities` row."""

    data_dir: Path
    credential_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.credential_ref, str) or not re.fullmatch(
            r"[0-9a-f]{32}", self.credential_ref
        ):
            raise ValueError("Invalid community credential reference")

    @property
    def root(self) -> Path:
        return Path(self.data_dir) / HOMES / self.credential_ref


def _ensure_private(paths) -> None:
    for path in paths:
        try:
            if path.is_symlink():
                raise IdentityUnavailable("unavailable", "Community identity directory is unsafe")
            ensure_user_private_directory(path)
            metadata = os.lstat(path)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise IdentityUnavailable("unavailable", "Community identity directory is unsafe")
            if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
                raise IdentityUnavailable(
                    "unavailable", "Community identity directory is not private"
                )
        except OSError as error:
            raise IdentityUnavailable(
                "unavailable", "Unable to prepare community identity"
            ) from error


def homes_directory(data_dir: Path) -> Path:
    """The private parent of every home; it also holds the lock all identity changes share."""
    homes = Path(data_dir) / HOMES
    _ensure_private((homes.parent, homes))
    return homes


def private_directory(home: IdentityHome) -> Path:
    homes_directory(home.data_dir)
    _ensure_private((home.root,))
    return home.root


@contextmanager
def locked(root: Path, *, lock_name: str = LOCK_FILE, blocking: bool = True):
    path = root / lock_name
    with _THREAD_LOCK:
        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            metadata = os.fstat(descriptor)
            current = os.lstat(path)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise IdentityUnavailable("unavailable", "Community identity lock is unsafe")
            if metadata.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            if not _lock(descriptor, exclusive=True, blocking=blocking):
                raise IdentityUnavailable("pending", "Another identity operation is running")
        except IdentityUnavailable:
            if descriptor is not None:
                os.close(descriptor)
            raise
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            raise IdentityUnavailable("unavailable", "Unable to lock community identity") from error
        try:
            yield
        finally:
            _unlock(descriptor)
            os.close(descriptor)


def read_file(path: Path, limit: int) -> bytes:
    try:
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise IdentityUnavailable("corrupt", "Community identity file is unsafe")
        if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
            raise IdentityUnavailable("corrupt", "Community identity file is not private")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise IdentityUnavailable("corrupt", "Community identity file changed during read")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                contents = handle.read(limit + 1)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise IdentityUnavailable("unavailable", "Unable to read community identity") from error
    if len(contents) > limit:
        raise IdentityUnavailable("corrupt", "Community identity file exceeds size limit")
    return contents
