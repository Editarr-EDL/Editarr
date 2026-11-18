"""Move a user's personal publications to a new export ID without losing the old files.

`plan_moves` reads every personal publication targeting an account and decides its
new path under the new export ID, refusing collisions and unfinished regeneration.
The account-link transition stages each new file (`write_new`), records the staged
file's identity, places it (`place_new`), switches the rows in the identity
transaction (`switch_rows`), and only then removes the old files (`remove_old`).
`discard_new` undoes a placement. An old file is recognized by its ownership digest;
a new file only by the recorded identity of the file Editarr created plus its digest,
because a planned digest is not evidence of a write. Recovery removes either file
only under the shared removal rule (`removable`). A repeat after a crash finds
nothing left to do. Callers hold the export publication lock across all of these steps.
"""

import errno
import json
import os
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from stat import S_ISREG

from sqlalchemy.orm import Session

from scanner.db import MediaItemExportRow
from scanner.formats.export_layout import ExportId, personal_export_path
from scanner.models import ExportFormat
from scanner.persistence.archive import archived_reads
from scanner.persistence.rows.db_export_regenerations import RegenerationState
from scanner.persistence.rows.db_policies import ExportScope
from scanner.runtime.durability import sync_directory

from .export_publication import (
    ExportConflict,
    check_export_components,
    prepare_export_directory,
    resolve_directory,
    safe_destination,
)
from .export_recovery import OWNED_DIGEST, _journal, digest, owned_digest

UNFINISHED = frozenset(
    {RegenerationState.QUEUED, RegenerationState.RUNNING, RegenerationState.WITHDRAWING}
)


@dataclass(frozen=True)
class PlannedMove:
    """One personal publication and where it moves; `conflict` names why it cannot."""

    export_id: int
    format: ExportFormat
    media_item_id: str
    media_source_id: str
    publication_revision: int
    media: Path
    old_path: Path
    new_path: Path
    old_sha256: str | None
    content: bytes | None
    conflict: str | None

    @property
    def new_sha256(self) -> str | None:
        return digest(self.content)

    def summary(self) -> dict:
        """Identifiers and the decision, never host paths or content."""
        return {
            "export_id": self.export_id,
            "format": self.format.value,
            "media_item_id": self.media_item_id,
            "media_source_id": self.media_source_id,
            "publication_revision": self.publication_revision,
            "action": "move" if self.content is not None else "record",
            "conflict": self.conflict,
        }


def personal_publications(db: Session, user_id: int) -> list[MediaItemExportRow]:
    return (
        db.query(MediaItemExportRow)
        .filter(
            MediaItemExportRow.scope == ExportScope.PERSONAL,
            MediaItemExportRow.target_user_id == user_id,
            MediaItemExportRow.snapshot_json.is_not(None),
        )
        .order_by(MediaItemExportRow.id)
        .all()
    )


def _conflict(db: Session, row: MediaItemExportRow, media: Path, new_id: ExportId, new_path):
    job = row.regeneration
    if job is not None and not job.deleted and job.state in UNFINISHED:
        return "export_regeneration_pending"
    old = Path(row.destination_path)
    if _journal(old).exists() or _journal(old).is_symlink():
        return "export_recovery_required"
    if recorded(db, new_path, excluding=row.id):
        return "export_filename_collision"
    if row.content_bytes is None:
        return _occupied(new_path)
    try:
        if safe_destination(media, row.format, new_id, require_media=False) != new_path:
            return "export_destination_changed"
    except ExportConflict as conflict:
        return conflict.code
    except (OSError, ValueError):
        return "export_destination_unavailable"
    return _occupied(new_path)


def _occupied(new_path: Path) -> str | None:
    """Any entry or journal at the new path is not Editarr's, even with matching bytes."""
    if os.path.lexists(new_path) or os.path.lexists(_journal(new_path)):
        return "export_file_collision"
    return None


def plan_moves(db: Session, user_id: int, new_id: ExportId) -> tuple[PlannedMove, ...]:
    """Decide every personal publication's move to `new_id` from current rows and files.

    Reads only. A publication without served content (withdrawn or a deleted record)
    moves its record and needs no new file.
    """
    moves = []
    for row in personal_publications(db, user_id):
        old_path = Path(row.destination_path)
        media_name = Path(json.loads(row.snapshot_json)["media_path"]).name
        media = old_path.parent.parent.parent / media_name
        new_path = personal_export_path(media, new_id, row.format)
        moves.append(
            PlannedMove(
                export_id=row.id,
                format=row.format,
                media_item_id=row.media_item_id,
                media_source_id=row.media_source_id,
                publication_revision=row.publication_revision,
                media=media,
                old_path=old_path,
                new_path=new_path,
                old_sha256=owned_digest(row),
                content=row.content_bytes,
                conflict=_conflict(db, row, media, new_id, new_path),
            )
        )
    shared = Counter(move.new_path for move in moves)
    return tuple(
        replace(move, conflict="export_filename_collision")
        if shared[move.new_path] > 1 and move.conflict is None
        else move
        for move in moves
    )


def _staging(destination: Path) -> Path:
    """The one temporary name a link writes `destination` through, so rollback finds it."""
    return destination.with_name(f".{destination.name}.editarr-link.tmp")


def _file_identity(status: os.stat_result) -> str:
    """The file's inode number as text.

    The device number is left out because a remount can renumber it. The path already
    fixes the file system, since it is confined to one media directory.
    """
    return str(status.st_ino)


def _temporary(destination: Path, content: bytes) -> str:
    """Durably write `content` at the staging name, which must not exist yet, and return
    the created file's identity (`_file_identity`).

    Exclusive creation makes the staged file one Editarr created, and the identity comes
    from the descriptor that created it, so it is evidence of Editarr's write. Recovery
    removes any earlier attempt's file first.
    """
    path = _staging(destination)
    # O_EXCL alone refuses a pre-existing link where O_NOFOLLOW is unavailable (Windows).
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            return _file_identity(os.fstat(stream.fileno()))
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _collision() -> ExportConflict:
    return ExportConflict(
        "export_file_collision", "An unrecognized file occupies the export destination"
    )


def write_new(move: PlannedMove, new_id: ExportId) -> str | None:
    """Durably stage `move`'s content beside its new path, refusing any existing entry.

    Returns the staged file's identity, which the caller records before `place_new`
    moves the file into place, or None for a move without content.
    """
    if move.content is None:
        return None
    prepare_export_directory(move.media, move.format, new_id, move.new_path, require_media=False)
    if os.path.lexists(move.new_path):
        raise _collision()
    return _temporary(move.new_path, move.content)


_NO_HARD_LINKS = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK})


def _place(staging: Path, destination: Path) -> None:
    """Give `staging`'s file the name `destination` without replacing any entry there.

    A hard link refuses an existing entry atomically. A file system without hard links
    falls back to checking, then renaming, which leaves a race with another writer.
    """
    try:
        os.link(staging, destination)
    except FileExistsError:
        raise _collision() from None
    except OSError as error:
        if error.errno not in _NO_HARD_LINKS:
            raise
        if os.path.lexists(destination):
            raise _collision() from None
        os.rename(staging, destination)


def place_new(new_path: Path) -> None:
    """Durably place the staged file at `new_path`, refusing any existing entry."""
    staging = _staging(new_path)
    _place(staging, new_path)
    staging.unlink(missing_ok=True)
    sync_directory(new_path.parent)


def _confined(path: Path) -> bool:
    """Whether `path` is still the export-layout entry it names, by `safe_destination`'s rule.

    Its media directory must resolve to itself and no entry below it may be a symbolic
    link, so a redirected path is never read or removed as Editarr's. Raises
    FileNotFoundError when the media directory is gone and another OSError, never
    absence, when it cannot be resolved (a symbolic-link loop is ELOOP).
    """
    media_directory = path.parents[2]
    if resolve_directory(media_directory) != media_directory:
        return False
    try:
        check_export_components(media_directory, path)
    except ValueError:
        return False
    return True


def _recognize(path: Path, expected: str | None, identity: str | None = None) -> str:
    """Return `owned` when `path` is a confined regular file with `expected` bytes (and,
    when given, the file `identity`), else `absent` or `kept`. Errors other than ENOENT
    propagate; they never count as absence.
    """
    try:
        if not _confined(path):
            return "kept"
        status = os.lstat(path)
    except FileNotFoundError:
        return "absent"
    if expected is None or not S_ISREG(status.st_mode):
        return "kept"
    if identity is not None and _file_identity(status) != identity:
        return "kept"
    if digest(path.read_bytes()) != expected:
        return "kept"
    return "owned"


def holds_owned(path: Path, expected: str | None) -> bool:
    """Whether `remove_old(path, expected)` would remove a file."""
    return _recognize(path, expected) == "owned"


def holds_placed(new_path: Path, new_sha256: str | None, identity: str | None) -> bool:
    """Whether `new_path` holds exactly the file Editarr staged as `identity`."""
    return identity is not None and _recognize(new_path, new_sha256, identity) == "owned"


def _remove_recognized(path: Path, expected: str | None, identity: str | None = None) -> str:
    """Remove `path` only when `_recognize` finds it owned.

    Returns `removed`, `absent`, or `kept`. Errors other than ENOENT propagate so the
    caller retries later; they never count as absence.
    """
    outcome = _recognize(path, expected, identity)
    if outcome != "owned":
        return outcome
    path.unlink()
    sync_directory(path.parent)
    return "removed"


def _prune(path: Path) -> None:
    """Drop `path`'s per-ID export directory once it is empty; anything else stays."""
    try:
        if _confined(path):
            path.parent.rmdir()
            sync_directory(path.parent.parent)
    except OSError:
        pass


def _remove_staging(new_path: Path) -> None:
    """Remove a regular file at `new_path`'s staging name, whatever it holds.

    The name is reserved for link writes made under the export publication lock, so a
    file there is a leftover of an interrupted link even when the crash cut its write
    short.
    """
    staging = _staging(new_path)
    try:
        if not _confined(staging):
            return
        mode = os.lstat(staging).st_mode
    except FileNotFoundError:
        return
    if S_ISREG(mode):
        staging.unlink()
        sync_directory(staging.parent)


def discard_new(new_path: Path, new_sha256: str | None, identity: str | None) -> None:
    """Undo `write_new` and `place_new`: remove the staging file, and the new file only
    when it is the recorded staged file `identity` with the bytes written. Without a
    recorded identity nothing was placed, so any file at the new path is someone else's.
    """
    if new_sha256 is not None:
        if identity is not None:
            _remove_recognized(new_path, new_sha256, identity)
        _remove_staging(new_path)
        _prune(new_path)


def recorded(db: Session, path: Path | str, *, excluding: int | None = None) -> bool:
    """Whether any publication row other than `excluding` records `path`, archived or not."""
    with archived_reads(db):
        query = db.query(MediaItemExportRow.id).filter_by(destination_path=str(path))
        if excluding is not None:
            query = query.filter(MediaItemExportRow.id != excluding)
        return query.first() is not None


def serves(db: Session, move) -> bool:
    """Whether `move`'s publication keeps its committed bytes on disk without `move`'s
    unrecorded copies: it was deleted or withdrawn, or the path it records now holds its
    committed digest. Never writes. Raises OSError when that path cannot be inspected.
    """
    with archived_reads(db):
        row = db.get(MediaItemExportRow, move.export_id) if move.export_id is not None else None
    if row is None or row.content_bytes is None:
        return True
    return _recognize(Path(row.destination_path), digest(row.content_bytes)) == "owned"


def removable(db: Session, move, path: Path | str) -> bool:
    """The shared removal rule for `move`'s old or new file at `path` (rulings
    wave13-mu38-failsafe-recovery and wave13-mu38-defer-restore).

    No publication records `path`, and the publication's content stays at the path it
    records (`serves`). The removal itself then proves Editarr's ownership by digest, and
    for a placed file by inode (`remove_old`, `discard_new`). A restored database can
    disagree with the disk, so without this rule recovery could remove the only usable
    copy.
    """
    return not recorded(db, path) and serves(db, move)


def discard_moved(db: Session, move) -> None:
    """Undo `move`'s placement under the shared removal rule; a file it keeps stays."""
    new_path = Path(move.new_path)
    if removable(db, move, new_path):
        discard_new(new_path, move.new_sha256, move.new_file_id)


def remove_old(old_path: Path, old_sha256: str | None) -> str:
    """Remove the old file when it holds the bytes Editarr wrote there."""
    outcome = _remove_recognized(old_path, old_sha256)
    _prune(old_path)
    return outcome


def switch_rows(db: Session, moves: tuple[PlannedMove, ...], new_id: ExportId) -> None:
    """Point each moved publication at its new path in the caller's transaction.

    The publication UUID is kept and its revision advances, so a client holding the
    old revision must reread before deleting. A record-only move drops the retained
    ownership digest: it belongs to the old path, where the move row keeps it, and
    nothing was written at the new path.
    """
    for move in moves:
        row = db.get(MediaItemExportRow, move.export_id)
        if row is None or row.publication_revision != move.publication_revision:
            raise ExportConflict("export_changed", "A personal export changed during linking")
        snapshot = json.loads(row.snapshot_json)
        snapshot["target_export_id"] = new_id.value
        if move.content is None:
            snapshot.pop(OWNED_DIGEST, None)
        row.snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        row.target_export_id = new_id.value
        row.destination_path = str(move.new_path)
        row.publication_revision += 1
    db.flush()
