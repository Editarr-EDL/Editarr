"""Recover interrupted sidecar replacement against the committed export snapshot."""

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from scanner.runtime.durability import sync_directory as _sync


class ExportRecoveryRequired(ValueError):
    """A retained backup or unexpected file needs recovery before another write."""


@contextmanager
def publication_lock(db):
    """Serialize canonical writers before BEGIN IMMEDIATE through post-commit cleanup."""
    from scanner.runtime.maintenance import _lock, _open_lock, _unlock
    from scanner.runtime.runtime_paths import ensure_user_private_directory

    if db.info.get("export_publication_lock"):
        yield
        return
    if db.new or db.dirty or db.deleted:
        raise ValueError("Acquire the publication lock before staging database changes")
    database = db.get_bind().url.database
    if not database or database == ":memory:":
        raise ValueError("Publication requires a durable database")
    db.rollback()
    directory = ensure_user_private_directory(Path(database).resolve().parent / "maintenance")
    descriptor = _open_lock(directory / "exports.lock")
    acquired = False
    try:
        acquired = _lock(descriptor, exclusive=True, blocking=True)
        if not acquired:
            raise ExportRecoveryRequired("Unable to acquire the export publication lock")
        db.info["export_publication_lock"] = True
        yield
    finally:
        db.info.pop("export_publication_lock", None)
        if acquired:
            _unlock(descriptor)
        os.close(descriptor)


def digest(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


OWNED_DIGEST = "owned_sha256"


def owned_digest(row) -> str | None:
    """Digest of the last sidecar Editarr wrote at `row`'s retained path.

    Serving rows own their committed bytes. Withdrawal keeps the digest in the snapshot,
    so bytes that reappear at the retained path are still recognized as Editarr's.
    """
    if row.content_bytes is not None:
        return digest(row.content_bytes)
    return json.loads(row.snapshot_json).get(OWNED_DIGEST) if row.snapshot_json else None


def deleted(row) -> bool:
    """Whether `row` records an explicit DELETE made while its retained path was absent."""
    return row.regeneration is not None and row.regeneration.deleted


def retained_path_absent(destination: Path) -> bool:
    """Prove absence only by ENOENT at the retained path and its recovery journal.

    Any other error, such as EACCES or EIO, propagates so the caller defers.
    """
    for path in (destination, _journal(destination)):
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        return False
    return True


def ensure_export_directory(directory: Path, *, media_directory: Path) -> None:
    """Persist canonical ancestors on every attempt, including entries left by a failed sync."""
    relative = directory.relative_to(media_directory)
    directory.mkdir(parents=True, exist_ok=True)
    for parent in relative.parents:
        _sync(media_directory / parent)


def _temporary(destination: Path, content: bytes, suffix: str) -> Path:
    stream = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=suffix,
        delete=False,
    )
    path = Path(stream.name)
    try:
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # The handle is closed first because Windows cannot unlink an open file.
        path.unlink(missing_ok=True)
        raise
    return path


def _journal(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.editarr-recovery.json")


def committed_export_content(db, export_id: int) -> bytes | None:
    """Resolve an uncertain commit from a fresh connection after releasing the writer."""
    from sqlalchemy import select

    from scanner.db import MediaItemExportRow

    db.rollback()
    with db.get_bind().connect() as connection:
        return connection.execute(
            select(MediaItemExportRow.content_bytes).where(MediaItemExportRow.id == export_id)
        ).scalar_one_or_none()


def recover_sidecar(destination: Path, committed: bytes | None, *, owned=frozenset()) -> None:
    """Reconcile only a recognizable interrupted write, under the database writer lock.

    `owned` holds the committed ownership digests for `destination`. A null commit that
    matches neither journal digest is admitted only when the journal replaced owned
    bytes, as after withdrawal or a deleted record; the file is then removed.
    Version-1 journals without a temporary basename remain readable.
    """
    journal = _journal(destination)
    if not journal.exists() and not journal.is_symlink():
        return
    if journal.is_symlink() or not journal.is_file():
        raise ExportRecoveryRequired("Export recovery journal is not a regular file")
    try:
        state = json.loads(journal.read_bytes())
        backup_name = state["backup"]
        temporary_name = state.get("temporary")
        if state["version"] != 1 or any(
            name is not None
            and (
                not isinstance(name, str)
                or Path(name).name != name
                or not name.startswith(f".{destination.name}.")
                or not name.endswith(suffix)
            )
            for name, suffix in ((backup_name, ".bak"), (temporary_name, ".tmp"))
        ):
            raise ValueError("Invalid recovery record")
        backup = destination.parent / backup_name if backup_name else None
        pending = destination.parent / temporary_name if temporary_name else None
        if any(path is not None and path.is_symlink() for path in (destination, backup, pending)):
            raise ValueError("Symbolic recovery file")
        actual = destination.read_bytes() if destination.exists() else None
        expected = digest(committed)
        if expected not in {state["old"], state["new"]} and not (
            committed is None and state["old"] is not None and state["old"] in owned
        ):
            raise ValueError("Committed content does not match recovery record")
        if digest(actual) not in {state["old"], state["new"], expected}:
            raise ValueError("Export changed outside the interrupted operation")
        if actual != committed:
            if committed is None:
                destination.unlink(missing_ok=True)
            else:
                temporary = _temporary(destination, committed, ".tmp")
                try:
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
            _sync(destination.parent)
        if backup is not None:
            backup.unlink(missing_ok=True)
        if pending is not None:
            pending.unlink(missing_ok=True)
        journal.unlink()
        _sync(destination.parent)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ExportRecoveryRequired("Export recovery requires the retained backup") from exc


@contextmanager
def recoverable_sidecar(
    destination: Path, content: bytes | None, *, journaled=False, committed_content=None
):
    """Retain MCF-07's rollback backup until the file and database change both succeed."""
    temporary = backup = None
    replaced = preserve = False
    existed = destination.exists()
    journal = _journal(destination) if journaled else None
    try:
        if content is not None:
            temporary = _temporary(destination, content, ".tmp")
        if existed:
            backup = _temporary(destination, b"", ".bak")
            with destination.open("rb") as source, backup.open("wb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
        if journal is not None:
            old = backup.read_bytes() if backup is not None else None
            state = json.dumps(
                {
                    "version": 1,
                    "old": digest(old),
                    "new": digest(content),
                    "backup": backup.name if backup is not None else None,
                    "temporary": temporary.name if temporary is not None else None,
                },
                sort_keys=True,
            ).encode("ascii")
            with journal.open("xb") as stream:
                stream.write(state)
                stream.flush()
                os.fsync(stream.fileno())
            _sync(destination.parent)
        if content is None:
            destination.unlink(missing_ok=True)
        else:
            os.replace(temporary, destination)
            temporary = None
        replaced = True
        _sync(destination.parent)
        yield
    except BaseException:
        if replaced:
            try:
                committed = committed_content() if committed_content is not None else None
                if committed_content is not None and committed == content:
                    pass
                elif backup is not None:
                    os.replace(backup, destination)
                    backup = None
                elif not existed:
                    destination.unlink(missing_ok=True)
                _sync(destination.parent)
            except Exception as exc:
                preserve = True
                raise ExportRecoveryRequired(
                    f"Unable to restore the previous {destination.suffix[1:].upper()} "
                    "after export failure; recovery backup retained"
                ) from exc
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if not preserve:
            for path in (backup, journal):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
