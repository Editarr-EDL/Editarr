"""Write a publication's stored committed bytes back to the path it records.

The owner's `editarr admin write-export` (MU-38, ruling wave13-mu38-defer-restore) uses
it when a publication's file is missing. It holds the export publication lock and
SQLite's write lock, as regeneration does, so it runs beside the app, and it confirms
under both that the actor is still the owner. It never replaces a directory entry: a
path that already holds the committed bytes is left as it is, any other entry is
refused, and an empty path is created exclusively. Missing export directories are
created by pathname (`prepare_export_directory`); placement then walks down from the
media directory through directory descriptors opened without following symbolic links,
so a link that appears after the pathname checks cannot carry the bytes out of the
export layout; a platform without those descriptors is refused. It runs no export
recovery: a pending recovery journal, found through the same descriptors, is refused as
`export_recovery_required` so MU-31's sweep or a publish finishes the interrupted write.
It never creates the media directory and changes no row except its audit event.

The event is staged before the file is placed. A failure or interrupt before the commit
returns removes the file this command created while it still holds the committed bytes
or a prefix of them. Two cases leave the file and its event out of step: a hard crash
in that window leaves the file unaudited, and an interrupt that lands as the commit
returns removes the file under a succeeded event. With hard links the name only ever
holds the complete bytes, so a rerun records them as already present or writes them
again, and once it succeeds it removes leftover temporary files that hold a prefix of
them. Without hard links the name is written in place, so a hard crash, or an interrupt
between creating the name and recording its descriptor, can leave a partial file there,
which a rerun refuses as `export_file_collision` until the operator deletes it.
"""

import errno
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from scanner.db import MediaItemExportRow
from scanner.formats.export_layout import parse_export_id
from scanner.persistence.rows.db_policies import ExportScope
from scanner.persistence.write_lock import write_lock
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.identity import UserId
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import record_event, stage_event
from scanner.security.auth_repository import owner_id

from .export_identity_moves import recorded
from .export_publication import (
    ExportConflict,
    filename_collision,
    prepare_export_directory,
    resolve_directory,
    safe_destination,
)
from .export_recovery import publication_lock

NOT_FOUND = AccessErrorCode.NOT_FOUND.value
NOT_OWNER = AccessErrorCode.CAPABILITY_DENIED.value
WITHDRAWN = "export_withdrawn"
COLLISION = "export_file_collision"
CHANGED = "export_destination_changed"
UNAVAILABLE = "export_destination_unavailable"
RECOVERY_REQUIRED = "export_recovery_required"
FILENAME_COLLISION = "export_filename_collision"
WRITE_FAILED = "write_failed"

ANCHORED = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NONBLOCK")
    and {os.open, os.stat, os.unlink, os.link} <= os.supports_dir_fd
    and os.listdir in os.supports_fd
)
_DIRECTORY = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_NEW_FILE = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
_EXISTING_FILE = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
_NO_HARD_LINKS = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK})
_TEMPORARY = re.compile(r"\.editarr-write-export-[0-9a-f]{32}\.tmp")


class WriteRefused(Exception):
    """Nothing was written; `code` is the audited refusal reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class WriteUnrecorded(Exception):
    """The file was created but its audit event did not commit.

    `removed` is False only when the entry at `path` is no longer the file this command
    created holding the committed bytes or a prefix of them, for example after an edit;
    it is then left alone.
    """

    def __init__(self, path: Path, *, removed: bool):
        super().__init__(str(path))
        self.path, self.removed = path, removed


def _event(row: MediaItemExportRow | None, export_id: int, actor_id: int, reason=None):
    """Without a `row` (no such publication) the event names only the ID and reason."""
    personal = row is not None and row.scope == ExportScope.PERSONAL
    details = {}
    if row is not None:
        details = {
            "format": row.format.value,
            "target_kind": "personal" if personal else "item_default",
        }
    if reason is not None:
        details["reason"] = reason
    return AuditEvent(
        occurred_at=datetime.now(UTC),
        actor=UserId(actor_id),
        credential=None,
        action=AuditAction.EXPORT_WRITE_COMMITTED,
        outcome=AuditOutcome.SUCCEEDED if reason is None else AuditOutcome.FAILED,
        target_user=UserId(row.target_user_id) if personal else None,
        media_key=row.media_item_id if row is not None else None,
        resource=f"export:{export_id}",
        details=details,
    )


def _publication(db: Session, export_id: int, actor_id: int) -> MediaItemExportRow:
    if owner_id(db) != actor_id:
        raise WriteRefused(NOT_OWNER, "The account is no longer the owner")
    row = db.get(MediaItemExportRow, export_id)
    if row is None or row.snapshot_json is None:
        raise WriteRefused(NOT_FOUND, f"No publication has ID {export_id}")
    if row.content_bytes is None:
        raise WriteRefused(WITHDRAWN, "The publication has no committed bytes to write")
    return row


def _destination(db: Session, row: MediaItemExportRow) -> tuple[Path, tuple[str, ...]]:
    """The media directory and the recorded path's parts below it, once checked by name."""
    if not ANCHORED:
        raise WriteRefused(UNAVAILABLE, "This platform cannot write exports without links")
    media = Path(json.loads(row.snapshot_json)["media_path"])
    target = parse_export_id(row.target_export_id) if row.scope == ExportScope.PERSONAL else None
    try:
        destination = safe_destination(media, row.format, target, require_media=False)
        _check_records(db, row, destination)
        prepare_export_directory(media, row.format, target, destination, require_media=False)
        root = resolve_directory(media.parent)
        return root, destination.relative_to(root).parts
    except ExportConflict as conflict:
        raise WriteRefused(conflict.code, str(conflict)) from None
    except ValueError:
        raise WriteRefused(CHANGED, "Export destination changed") from None
    except OSError as error:
        raise WriteRefused(UNAVAILABLE, f"The export destination is unavailable: {error}") from None


def _check_records(db: Session, row: MediaItemExportRow, destination: Path) -> None:
    """Refuse a destination that another publication records or that `row` no longer does."""
    if recorded(db, destination, excluding=row.id):
        raise WriteRefused(FILENAME_COLLISION, str(filename_collision(db, destination, row.id)))
    if row.destination_path != str(destination):
        raise WriteRefused(CHANGED, "Export destination changed; resolve it first")


def _open_directory(root: Path, parts: tuple[str, ...]) -> int:
    """Open the export directory one component at a time, following no symbolic link."""
    try:
        descriptor = os.open(root, _DIRECTORY)
        for part in parts:
            try:
                child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            finally:
                os.close(descriptor)
            descriptor = child
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise WriteRefused(CHANGED, "Export destination changed") from None
        raise WriteRefused(UNAVAILABLE, f"The export destination is unavailable: {error}") from None
    return descriptor


def _read(descriptor: int, limit: int) -> bytes:
    """Up to `limit` bytes of the file behind `descriptor`, from its start."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    while limit > 0 and (chunk := os.read(descriptor, min(limit, 1 << 20))):
        chunks.append(chunk)
        limit -= len(chunk)
    return b"".join(chunks)


def _discard_temporaries(directory: int, content: bytes) -> None:
    """Remove what a crashed run of this command left under its temporary name.

    Such a leftover is the placed file itself or a partial copy, so it holds a prefix
    of `content`. A file under that name holding anything else is not this command's
    and stays. Callers run this only after a successful write, never on a refusal.
    """
    try:
        entries = [entry for entry in os.listdir(directory) if _TEMPORARY.fullmatch(entry)]
    except OSError:
        return
    for entry in entries:
        try:
            descriptor = os.open(entry, _EXISTING_FILE, dir_fd=directory)
        except OSError:
            continue
        try:
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                _remove(directory, entry, descriptor, content)
        except OSError:
            pass
        finally:
            os.close(descriptor)


def _recovery_pending(directory: int, name: str) -> bool:
    """Whether MU-31's recovery journal for `name` has any entry in `directory`."""
    try:
        os.stat(f".{name}.editarr-recovery.json", dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _present(directory: int, name: str, limit: int) -> bytes | None:
    """Up to `limit` bytes of the regular file at `name`, or None when nothing is there."""
    try:
        descriptor = os.open(name, _EXISTING_FILE, dir_fd=directory)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise WriteRefused(COLLISION, "A symbolic link occupies the export destination")
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise WriteRefused(COLLISION, "Something other than a file occupies the destination")
        return _read(descriptor, limit)
    finally:
        os.close(descriptor)


def _fill(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        view = view[os.write(descriptor, view) :]
    os.fsync(descriptor)


def _create(directory: int, name: str, content: bytes, placed: list[int]) -> None:
    """Place `content` at `name` without replacing anything.

    A hidden temporary file, whose name does not grow with `name`, is hard-linked into
    place, so `name` only ever appears complete. Where the filesystem has no hard links,
    `name` is created exclusively and written in place. The descriptor of the file that
    can stand at `name` goes into `placed` before that file can appear there, so the
    caller can remove it after any later failure or interrupt; the caller closes it.
    """
    temporary = f".editarr-write-export-{uuid4().hex}.tmp"
    descriptor = os.open(temporary, _NEW_FILE, 0o600, dir_fd=directory)
    try:
        _fill(descriptor, content)
        placed.append(descriptor)
        try:
            os.link(
                temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False
            )
        except OSError as error:
            if error.errno not in _NO_HARD_LINKS:
                raise
            placed.clear()
            placed.append(os.open(name, _NEW_FILE, 0o600, dir_fd=directory))
            _fill(placed[0], content)
    finally:
        _remove(directory, temporary, descriptor, None)
        if descriptor not in placed:
            os.close(descriptor)
    os.fsync(directory)
    entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if (
        not os.path.samestat(entry, os.fstat(placed[0]))
        or _read(placed[0], len(content) + 1) != content
    ):
        raise WriteRefused(WRITE_FAILED, "The new export changed before it was verified")


def _remove(directory: int, name: str, descriptor: int, content: bytes | None) -> bool:
    """Remove `name` only while it is still the file behind `descriptor`.

    With `content`, that file must also hold `content` or a prefix of it, as a file this
    command wrote does, so an edit is left in place.
    """
    try:
        entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not os.path.samestat(entry, os.fstat(descriptor)):
            return False
        if content is not None and not content.startswith(_read(descriptor, len(content) + 1)):
            return False
        os.unlink(name, dir_fd=directory)
        os.fsync(directory)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _place(db: Session, directory: int, name: str, content: bytes, path: Path) -> None:
    """Create `name` with `content` and commit the staged event.

    Until the commit returns, any failure or interrupt removes the created file while
    it is still this command's (`_remove`). A failed commit raises `WriteUnrecorded`.
    """
    placed: list[int] = []
    try:
        try:
            _create(directory, name, content, placed)
        except FileExistsError:
            raise WriteRefused(COLLISION, "A file appeared at the export destination") from None
        try:
            db.commit()
        except Exception as error:
            removed = _remove(directory, name, placed[0], content)
            raise WriteUnrecorded(path, removed=removed) from error
    except BaseException as error:
        if placed and not isinstance(error, WriteUnrecorded):
            _remove(directory, name, placed[0], content)
        raise
    finally:
        for descriptor in placed:
            os.close(descriptor)


def _write(db: Session, row: MediaItemExportRow, actor_id: int, root: Path, parts) -> bool:
    """Audit and create the file at `root/parts`; False when it already held the bytes."""
    content, name = row.content_bytes, parts[-1]
    directory = _open_directory(root, parts[:-1])
    try:
        if _recovery_pending(directory, name):
            raise WriteRefused(RECOVERY_REQUIRED, "An interrupted export write awaits recovery")
        present = _present(directory, name, len(content) + 1)
        if present is not None and present != content:
            raise WriteRefused(COLLISION, "An unrecognized file occupies the export destination")
        stage_event(db, _event(row, row.id, actor_id))
        if present is None:
            _place(db, directory, name, content, root.joinpath(*parts))
        else:
            db.commit()
    except OSError as error:
        raise WriteRefused(WRITE_FAILED, f"The export could not be written: {error}") from None
    else:
        _discard_temporaries(directory, content)
        return present is None
    finally:
        os.close(directory)


def write_committed(db: Session, export_id: int, actor_id: int) -> tuple[Path, bool]:
    """Write publication `export_id`'s committed bytes to its recorded path as `actor_id`.

    Returns the path and whether this call created the file. Raises `WriteRefused`,
    audited, when it cannot write there safely, and `WriteUnrecorded` when the audit
    event did not commit after the file was created.
    """
    with publication_lock(db), write_lock(db):
        try:
            row = _publication(db, export_id, actor_id)
            root, parts = _destination(db, row)
            return root.joinpath(*parts), _write(db, row, actor_id, root, parts)
        except WriteRefused as refusal:
            db.rollback()
            refuse(db, export_id, actor_id, refusal.code)
            raise


def refuse(db: Session, export_id: int, actor_id: int, reason: str) -> None:
    """Record that `actor_id` was refused writing publication `export_id`."""
    record_event(db, _event(db.get(MediaItemExportRow, export_id), export_id, actor_id, reason))
