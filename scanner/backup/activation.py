"""Journaled, offline activation of a validated restore into an empty installation."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from pathlib import Path

from scanner.persistence.migrations.adoption import HEAD_REVISION, _inspect_database
from scanner.runtime.maintenance import DatabaseMaintenance
from scanner.runtime.runtime_paths import ensure_user_private_directory
from scanner.security.private_identity_files import HOMES

from .activation_state import _checkpoint_target, _paths_need_review, _sanitize_db, _snapshot_db
from .archive import (
    DATABASE,
    HOME_MEMBER,
    IDENTITY_FILES,
    INCOMING_MEMBERS,
    BackupError,
    _digest,
)
from .inventory import EXTERNAL_FILES
from .move_report import move_disagreements
from .restore import RestorePreview, check_registrations, staged_identities
from .restore_target import require_empty_installation
from .snapshots import _sync_directory

JOURNAL = "restore-activation.json"
HOLD = "restore-reconciliation.json"
REPORT = "restore-report.json"
GENERATION = "restore-generation"
RECOVERY = "restore-recovery"
MARKER = b"editarr-restore-generation-v1\n"
# The empty installation keeps its own base URL and port (ruling wave14-mu40-deployment-binding).
DEPLOYMENT = "deployment.json"


def _copy(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise BackupError("Restore member must be a regular file")
    ensure_user_private_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise BackupError("Restore generation already contains a member")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    _sync_directory(destination.parent)


def _record(path: Path, data: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".restore-record-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
            json.dump(data, writer, sort_keys=True)
            writer.write("\n")
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _member_names(stage: Path) -> list[str]:
    """Every file a restore of `stage` may replace, database first."""
    homes = stage / HOMES
    references = sorted(path.name for path in homes.iterdir()) if homes.is_dir() else []
    return [
        DATABASE,
        *(name for name in EXTERNAL_FILES if name != DEPLOYMENT),
        *INCOMING_MEMBERS,
        *(f"{HOMES.as_posix()}/{ref}/{name}" for ref in references for name in IDENTITY_FILES),
    ]


def _known_names(names: object) -> bool:
    return (
        isinstance(names, list)
        and names[:1] == [DATABASE]
        and len(set(names)) == len(names)
        and all(
            isinstance(name, str)
            and (name in (*EXTERNAL_FILES, *INCOMING_MEMBERS) or HOME_MEMBER.fullmatch(name))
            for name in names[1:]
        )
    )


def _generation_paths(data_dir: Path, database_path: Path, journal: dict) -> tuple[Path, Path]:
    generation = data_dir / GENERATION
    if (
        not isinstance(journal, dict)
        or journal.get("version") != 1
        or journal.get("database") != str(database_path.resolve())
        or not _known_names(journal.get("files"))
        or not isinstance(journal.get("operation"), str)
        or len(journal["operation"]) != 32
        or any(character not in "0123456789abcdef" for character in journal["operation"])
        or not isinstance(journal.get("before"), dict)
        or set(journal["before"]) != set(journal["files"])
        or not generation.is_dir()
        or generation.is_symlink()
        or not (generation / ".marker").is_file()
        or (generation / ".marker").is_symlink()
        or (generation / ".marker").read_bytes() != MARKER
    ):
        raise BackupError("Unknown restore activation journal; inspect without modifying data")
    return generation / "before", generation / "after"


def _remove_pending(data_dir: Path, database_path: Path, names: list[str], operation: str) -> None:
    for name in names:
        target = database_path if name == DATABASE else data_dir / name
        pending = target.with_name(f".restore-{target.name}-{operation}")
        pending.unlink(missing_ok=True)


def _apply(
    generation: Path, data_dir: Path, database_path: Path, names: list[str], operation: str
) -> None:
    for name in names:
        target = database_path if name == DATABASE else data_dir / name
        original = generation / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise BackupError("Restore target is not a regular file")
        if original.is_file():
            ensure_user_private_directory(target.parent)
            pending = target.with_name(f".restore-{target.name}-{operation}")
            try:
                _copy(original, pending)
                os.replace(pending, target)
                _sync_directory(target.parent)
            except Exception:
                pending.unlink(missing_ok=True)
                raise
        else:
            if target.exists():
                target.unlink()
                _sync_directory(target.parent)


def recover_restore(database_path: Path, data_dir: Path, *, timeout: float = 10.0) -> bool:
    """Roll back an interrupted switch before admitting any database users."""
    data_dir, database_path = Path(data_dir), Path(database_path)
    with DatabaseMaintenance(data_dir).exclusive(timeout=timeout):
        path = data_dir / JOURNAL
        if not path.exists():
            generation = data_dir / GENERATION
            if generation.exists():
                if (
                    generation.is_symlink()
                    or not generation.is_dir()
                    or not (generation / ".marker").is_file()
                    or (generation / ".marker").is_symlink()
                    or (generation / ".marker").read_bytes() != MARKER
                ):
                    raise BackupError("Unknown restore generation; inspect without deleting")
                if (data_dir / HOLD).exists():
                    if (data_dir / RECOVERY).exists():
                        raise BackupError("Target recovery generation already exists")
                    inventory = json.loads((generation / "inventory.json").read_text())
                    before, _ = _generation_paths(data_dir, database_path, inventory)
                    for name, expected in inventory["before"].items():
                        member = before / name
                        if (
                            member.is_symlink()
                            or member.is_file() != (expected is not None)
                            or (expected is not None and _digest(member) != expected)
                        ):
                            raise BackupError("Target recovery generation is incomplete")
                    if inventory["before"][DATABASE] is not None:
                        _inspect_database(before / DATABASE)
                    generation.rename(data_dir / RECOVERY)
                    _sync_directory(data_dir)
                else:
                    shutil.rmtree(generation)
            return False
        journal = json.loads(path.read_text(encoding="utf-8"))
        before, _after = _generation_paths(data_dir, database_path, journal)
        names = journal["files"]
        for name in names:
            member = before / name
            expected = journal["before"][name]
            if (
                (expected is not None and (not isinstance(expected, str) or len(expected) != 64))
                or member.is_symlink()
                or (member.is_file() != (expected is not None))
                or (expected is not None and _digest(member) != expected)
            ):
                raise BackupError("Target recovery generation is incomplete; do not start")
        if journal["before"][DATABASE] is not None:
            _inspect_database(before / DATABASE)
        _remove_pending(data_dir, database_path, names, journal["operation"])
        _apply(before, data_dir, database_path, names, journal["operation"])
        if journal["before"][DATABASE] is not None:
            _inspect_database(database_path)
        _remove_pending(data_dir, database_path, names, journal["operation"])
        (data_dir / HOLD).unlink(missing_ok=True)
        (data_dir / REPORT).unlink(missing_ok=True)
        path.unlink()
        _sync_directory(data_dir)
        shutil.rmtree(data_dir / GENERATION)
        return True


def activate_restore(
    preview: RestorePreview,
    database_path: Path,
    data_dir: Path,
    *,
    confirmed: bool = False,
    timeout: float = 10.0,
) -> dict:
    """Switch a preflighted backup into an empty installation, with rollback on restart.

    Returns the restore report, also written to `restore-report.json`: the move
    records that disagree with the disk and the backup's deployment binding.
    """
    if confirmed is not True:
        raise BackupError("Restore requires explicit confirmation")
    database_path, data_dir = Path(database_path), Path(data_dir)
    if not preview.stage.is_dir() or preview.stage.is_symlink():
        raise BackupError("Restore preview has expired")
    with DatabaseMaintenance(data_dir).exclusive(timeout=timeout):
        if any((data_dir / name).exists() for name in (JOURNAL, GENERATION, HOLD, RECOVERY)):
            raise BackupError("Previous restore needs recovery before another activation")
        require_empty_installation(data_dir, database_path)
        if _inspect_database(preview.stage / DATABASE) != HEAD_REVISION:
            raise BackupError("Staged database is no longer valid")
        generation = data_dir / GENERATION
        ensure_user_private_directory(generation)
        (generation / ".marker").write_bytes(MARKER)
        (generation / ".marker").chmod(0o600)
        before = generation / "before"
        after = generation / "after"
        before.mkdir(mode=0o700)
        after.mkdir(mode=0o700)
        names = _member_names(preview.stage)
        operation = uuid.uuid4().hex
        published = False
        try:
            if database_path.exists() or database_path.is_symlink():
                _snapshot_db(database_path, before / DATABASE)
            for name in names[1:]:
                target = data_dir / name
                if target.exists() or target.is_symlink():
                    _copy(target, before / name)
            _snapshot_db(preview.stage / DATABASE, after / DATABASE)
            disagreements = move_disagreements(after / DATABASE)
            _sanitize_db(after / DATABASE, len(disagreements))
            for name in names[1:]:
                source = preview.stage / name if (preview.stage / name).is_file() else before / name
                if source.is_file():
                    _copy(source, after / name)
            staged_identities(after, [name for name in names if (after / name).is_file()])
            with closing(sqlite3.connect(after / DATABASE)) as restored:
                check_registrations(restored, after)
            report = {
                "version": 1,
                "move_disagreements": disagreements,
                "backup_deployment": preview.plan["backup_deployment"],
            }
            _inspect_database(after / DATABASE)
            _sync_directory(generation)
            if (before / DATABASE).is_file():
                _checkpoint_target(database_path)
            inventory = {
                "version": 1,
                "database": str(database_path.resolve()),
                "files": names,
                "operation": operation,
                "before": {
                    name: _digest(before / name) if (before / name).is_file() else None
                    for name in names
                },
            }
            _record(generation / "inventory.json", inventory)
            _record(data_dir / JOURNAL, inventory)
            published = True
            _apply(after, data_dir, database_path, names, operation)
            _record(
                data_dir / HOLD,
                {
                    "status": "pending_reconciliation",
                    "queue_paused": True,
                    "online_key_verification_required": True,
                    "path_review_required": _paths_need_review(after / DATABASE),
                    "connection_review_required": True,
                },
            )
            _record(data_dir / REPORT, report)
            (data_dir / JOURNAL).unlink()
            _sync_directory(data_dir)
            generation.rename(data_dir / RECOVERY)
            _sync_directory(data_dir)
        finally:
            if not published:
                shutil.rmtree(generation)
    return report
