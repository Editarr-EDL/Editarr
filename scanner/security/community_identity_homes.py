"""Move one community identity between the incoming directory and an account's home.

Upgrades and restores leave signing files in the incoming directory. One directory
rename publishes them in a home, so a crash leaves every file in exactly one of the
two places, and a retry finishes the move. A location that already holds identity
files is never overwritten.
"""

import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath

from .community_identity import _locked, holds_identity
from .private_identity_files import (
    DIRECTORY,
    IdentityHome,
    IdentityUnavailable,
    homes_directory,
    read_file,
)

# Lock files and unpublished atomic-write temporaries.
_DISPOSABLE = re.compile(
    r"(identity|rotation)\.lock|\.(identity\.json|current\.key|pending\.key)-.+"
)
_OWNER_REFERENCE = (
    "SELECT credential_ref FROM community_identities "
    "WHERE user_id IS NULL OR user_id = (SELECT id FROM users WHERE role = 'owner') "
    "ORDER BY user_id IS NULL LIMIT 1"
)
_STAGING = ".community-recovery"


def owner_home(data_dir: Path, database_path: Path) -> IdentityHome | None:
    """Read the owner's home, or the one awaiting the owner, from a database file.

    Return None when the database names no home, including before revision 0013.
    """
    database_path = Path(database_path)
    if not database_path.is_file() or database_path.is_symlink():
        return None
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as database:
        if not database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'community_identities'"
        ).fetchone():
            return None
        row = database.execute(_OWNER_REFERENCE).fetchone()
    return IdentityHome(Path(data_dir), row[0]) if row is not None else None


def owner_identity_root(data_dir: Path, database_path: Path) -> Path | None:
    """The directory holding the owner's identity files: incoming, the home, or none."""
    home = owner_home(data_dir, database_path)
    roots = [Path(data_dir) / DIRECTORY, *((home.root,) if home is not None else ())]
    if any(root.is_symlink() for root in roots):
        raise IdentityUnavailable("unavailable", "Community identity directory is unsafe")
    held = [root for root in roots if holds_identity(root)]
    if len(held) > 1:
        raise IdentityUnavailable("conflict", "Two locations hold community identity files")
    return held[0] if held else None


def _sync_directory(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _move(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        if (
            destination.is_symlink()
            or not destination.is_dir()
            or holds_identity(destination)
            or any(not _DISPOSABLE.fullmatch(entry.name) for entry in destination.iterdir())
        ):
            raise IdentityUnavailable("conflict", "Two locations hold community identity files")
        shutil.rmtree(destination)
    try:
        os.rename(source, destination)
        _sync_directory(destination.parent)
        _sync_directory(source.parent)
    except OSError as error:
        raise IdentityUnavailable("unavailable", "Unable to move community identity") from error


def adopt_incoming_identity(home: IdentityHome) -> bool:
    """Move incoming identity files into `home`; return whether anything moved."""
    incoming = Path(home.data_dir) / DIRECTORY
    with _locked(home.data_dir):
        if incoming.is_symlink():
            raise IdentityUnavailable("unavailable", "Community identity directory is unsafe")
        if not incoming.is_dir() or not holds_identity(incoming):
            return False
        _move(incoming, home.root)
        return True


def return_identity_to_incoming(home: IdentityHome) -> bool:
    """Move a home's files back to the incoming directory, where a restore replaces them.

    The next adoption moves whichever identity the restore leaves there into the home
    its database names. Return whether anything moved.
    """
    with _locked(home.data_dir):
        if home.root.is_symlink():
            raise IdentityUnavailable("unavailable", "Community identity directory is unsafe")
        if not home.root.is_dir() or not holds_identity(home.root):
            return False
        _move(home.root, Path(home.data_dir) / DIRECTORY)
        return True


@contextmanager
def recovery_destination(data_dir: Path):
    """Hold a recovery data directory's identity lock for one complete export.

    A competing export to the same directory waits, so the identity and database
    published there always come from one export.
    """
    with _locked(data_dir):
        yield


def _confirm_durable(incoming: Path) -> None:
    for entry in incoming.iterdir():
        if not _DISPOSABLE.fullmatch(entry.name):
            descriptor = os.open(entry, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    _sync_directory(incoming)
    _sync_directory(incoming.parent)


def publish_incoming_identity(data_dir: Path, files: tuple[tuple[str, bytes], ...]) -> None:
    """Write backup snapshot `files` into another data directory's incoming directory.

    Call inside `recovery_destination`. One rename publishes this export's own staging
    directory. A retry removes staging an interrupted export left, makes identical
    published files durable again, and refuses different ones.
    """
    incoming = Path(data_dir) / DIRECTORY
    wanted = {PurePosixPath(member).name: contents for member, contents in files}
    try:
        homes_directory(data_dir)
        if incoming.is_symlink():
            raise IdentityUnavailable("unavailable", "Community identity directory is unsafe")
        for entry in incoming.parent.iterdir():
            if entry.name.startswith(_STAGING):
                if entry.is_symlink() or not entry.is_dir():
                    raise IdentityUnavailable(
                        "unavailable", "Community identity directory is unsafe"
                    )
                shutil.rmtree(entry)
        if incoming.exists():
            held = {
                entry.name: read_file(entry, 16_384)
                for entry in incoming.iterdir()
                if not _DISPOSABLE.fullmatch(entry.name)
            }
            if held != wanted:
                raise IdentityUnavailable(
                    "conflict", "Another community identity is already in the recovery directory"
                )
            _confirm_durable(incoming)
            return
        staging = Path(tempfile.mkdtemp(prefix=f"{_STAGING}-", dir=incoming.parent))
        for name, contents in wanted.items():
            descriptor = os.open(
                staging / name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
        _sync_directory(staging)
        os.rename(staging, incoming)
        _sync_directory(incoming.parent)
    except OSError as error:
        raise IdentityUnavailable("unavailable", "Unable to copy community identity") from error
