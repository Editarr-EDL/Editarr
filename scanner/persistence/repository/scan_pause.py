"""Persist explicit scan pauses independently of the API and worker lifetimes."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import ScanQueueRow, ScanRow
from scanner.persistence.write_lock import write_lock

from .scan_timing import stop_scan_attempt


def pause_scan_queue_item(db: Session, queue_id: int) -> str | None:
    """Hold a queued scan or ask its current worker to stop at a checkpoint."""
    with write_lock(db):
        entry = db.get(ScanQueueRow, queue_id)
        if entry is None:
            db.rollback()
            return None
        row = entry.scan
        if row.status == "queued":
            mark_paused(row)
        elif row.status == "scanning":
            row.status = "pausing"
            row.progress_message = "Pausing at the latest checkpoint"
            row.updated_at = datetime.now(UTC)
        elif row.status not in {"pausing", "paused"}:
            db.rollback()
            return None
        status = row.status
        db.commit()
        return status


def mark_paused(row: ScanRow) -> None:
    """Settle a pause after no worker can still be writing its attempt."""
    stop_scan_attempt(row, None)
    row.status = "paused"
    row.progress_stage = "paused"
    row.progress_message = "Paused — ready to resume from the latest checkpoint"
    row.error_message = None
    clear_worker_reservation(row)
    row.updated_at = datetime.now(UTC)


def clear_worker_reservation(row: ScanRow) -> None:
    """Fence a stopped worker without changing its historical timing."""
    row.worker_pid = None
    row.worker_pgid = None
    row.worker_token = None
    row.worker_heartbeat_at = None


def is_scan_pause_requested(db: Session, scan_id: str) -> bool:
    """Read the live status even when a worker session has the scan cached."""
    return db.scalar(select(ScanRow.status).where(ScanRow.id == scan_id)) == "pausing"


def worker_owns_scan(db: Session, scan_id: str, token: str) -> bool:
    """An old worker may never write after losing its persisted reservation."""
    return db.scalar(select(ScanRow.worker_token).where(ScanRow.id == scan_id)) == token
