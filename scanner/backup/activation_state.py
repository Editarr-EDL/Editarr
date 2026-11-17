"""Prepare and validate a restored SQLite generation before publication."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from scanner.db import ScanRow
from scanner.persistence.migrations.adoption import _inspect_database
from scanner.persistence.repository.activity_ownership import (
    activity_owner,
    assign_awaiting_activity,
)
from scanner.persistence.repository.scan_timing import stop_scan_attempt
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.audit.actions import ActorKind, AuditAction
from scanner.security.audit.store import stage_event

from .archive import BackupError
from .snapshots import _sync_directory, _validate


def _sync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    _sync_directory(path.parent)


def _snapshot_db(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise BackupError("Active SQLite file is missing or unsafe")
    with closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as active:
        with closing(sqlite3.connect(target)) as snapshot:
            active.backup(snapshot)
            snapshot.execute("PRAGMA journal_mode=DELETE")
            _validate(snapshot)
    target.chmod(0o600)
    _sync_file(target)


def _sanitize_db(path: Path, move_disagreements: int) -> None:
    """Revoke sessions, stop restored workers, and record the restore in the database."""
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(path), poolclass=NullPool)
    try:
        with Session(engine) as db:
            attempts = db.scalars(
                select(ScanRow).where(ScanRow.worker_attempt_started_at.is_not(None))
            )
            for scan in attempts:
                stop_scan_attempt(scan, None)
            db.flush()
            run = db.connection().exec_driver_sql
            run("DELETE FROM auth_sessions")
            run(
                "UPDATE scans SET worker_pid=NULL, worker_pgid=NULL, worker_token=NULL, "
                "worker_heartbeat_at=NULL, "
                "status='orphaned' WHERE status IN ('scanning', 'cancelling')"
            )
            run(
                "UPDATE scans SET worker_pid=NULL, worker_pgid=NULL, worker_token=NULL, "
                "worker_heartbeat_at=NULL "
                "WHERE worker_pid IS NOT NULL OR worker_pgid IS NOT NULL "
                "OR worker_token IS NOT NULL"
            )
            run(
                "UPDATE scans SET status='orphaned' WHERE status='queued' "
                "AND id IN (SELECT scan_id FROM scan_queue)"
            )
            # A Jellyfin read from the replaced database must not apply here.
            run("UPDATE account_settings SET jellyfin_read_epoch = NULL")
            db.commit()
        with closing(sqlite3.connect(path)) as database:
            _validate(database)
        # A backup can hold an owner beside work still awaiting one.
        with engine.begin() as connection:
            owner = activity_owner(connection)
            if owner is not None:
                assign_awaiting_activity(connection, owner)
        with Session(engine) as db:
            event = AuditEvent(
                datetime.now(timezone.utc),
                None,
                None,
                AuditAction.BACKUP_RESTORE,
                AuditOutcome.SUCCEEDED,
                details={"stage": "restored", "move_disagreements": move_disagreements},
            )
            stage_event(db, event, actor_kind=ActorKind.SYSTEM)
            db.commit()
    finally:
        engine.dispose()
    _inspect_database(path)
    _sync_file(path)


def _paths_need_review(path: Path) -> bool:
    with closing(sqlite3.connect(path)) as database:
        return any(
            not Path(local_path).is_absolute() or not Path(local_path).is_dir()
            for (local_path,) in database.execute("SELECT local_path FROM path_mappings")
        )


def _checkpoint_target(path: Path) -> None:
    try:
        with closing(sqlite3.connect(path, timeout=0.1)) as database:
            result = database.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if result and result[0]:
                raise BackupError("Target WAL cannot be checkpointed")
            database.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.OperationalError as error:
        raise BackupError("Target WAL is held by an unmanaged database connection") from error
    if path.with_name(path.name + "-wal").exists():
        raise BackupError("Target WAL remains after checkpoint")
    _sync_file(path)
