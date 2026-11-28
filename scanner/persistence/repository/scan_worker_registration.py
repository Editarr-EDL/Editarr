"""Claim scan worker reservations and persist launch and worker process identities."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from scanner.db import ScanRow
from scanner.persistence.write_lock import write_lock

from .scan_timing import start_scan_attempt


def reserve_scan_worker(db: Session, scan_id: str, *, token: str) -> bool:
    """Claim ``scan_id`` for the worker identified by ``token`` when available."""
    now = datetime.now(timezone.utc)
    claimed = (
        db.query(ScanRow)
        .filter(ScanRow.id == scan_id, ScanRow.status == "queued")
        .update(
            {
                ScanRow.status: "scanning",
                ScanRow.progress_stage: "setup",
                ScanRow.progress_message: "Starting dedicated scan worker",
                ScanRow.worker_token: token,
                ScanRow.worker_heartbeat_at: now,
                ScanRow.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return claimed == 1


def record_scan_worker_process(
    db: Session, scan_id: str, *, token: str, pid: int, pgid: int | None
) -> bool:
    """Persist the operating-system process identity for a reserved scan worker."""
    with write_lock(db):
        row = db.get(ScanRow, scan_id)
        if not row or row.worker_token != token or row.status not in {
            "scanning", "pausing", "orphaned",
        }:
            db.rollback()
            return False
        now = datetime.now(timezone.utc)
        row.worker_pid = pid
        # POSIX uses a process group; Windows uses a named Job Object derived from
        # the scan ID and worker token.
        row.worker_pgid = pgid
        if row.status == "scanning":
            start_scan_attempt(row, now)
        row.worker_heartbeat_at = now
        row.updated_at = now
        db.commit()
        return True


def record_unconfirmed_scan_worker_process(
    db: Session, scan_id: str, *, token: str, pid: int, pgid: int | None,
    replace_launcher_pid: int | None = None,
) -> None:
    """Retain a launch PID for liveness without overwriting the worker's own identity."""
    with write_lock(db):
        row = db.get(ScanRow, scan_id)
        if (
            row is None
            or row.worker_token != token
            or row.status not in {"scanning", "pausing", "cancelling"}
            or row.worker_pid not in {None, replace_launcher_pid}
        ):
            db.rollback()
            return
        # A Windows launcher can be a redirector. Endpoint proof still must match
        # the PID before a controller may signal a worker via persisted identity.
        row.worker_pid = pid
        row.worker_pgid = pgid
        db.commit()
