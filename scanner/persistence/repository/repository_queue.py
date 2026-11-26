"""Repository operations for scan queue and worker checkpoints."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import ScanQueueRow, ScanRow
from scanner.models import ScanMode, ScanQueueItem
from scanner.persistence.write_lock import write_lock

from .repository_rows import _scan_queue_from_row
from .scan_timing import start_scan_attempt, stop_scan_attempt


def get_next_scan_queue(db: Session) -> ScanQueueItem | None:
    row = (
        db.query(ScanQueueRow)
        .join(ScanRow)
        .filter(ScanRow.status == "queued")
        .order_by(ScanQueueRow.sort_order, ScanQueueRow.id)
        .first()
    )
    return _scan_queue_from_row(row) if row else None


def get_active_scan_queue_item(db: Session) -> ScanQueueItem | None:
    row = (
        db.query(ScanQueueRow)
        .join(ScanRow)
        .filter(ScanRow.status.in_(("scanning", "orphaned", "cancelling")))
        .order_by(ScanQueueRow.sort_order, ScanQueueRow.id)
        .first()
    )
    return _scan_queue_from_row(row) if row else None


def scanning_scan_worker_processes(
    db: Session,
) -> list[tuple[str, int | None, int | None, str | None]]:
    rows = (
        db.query(ScanRow.id, ScanRow.worker_pid, ScanRow.worker_pgid, ScanRow.worker_token)
        .join(ScanQueueRow)
        .filter(ScanRow.status == "scanning")
        .all()
    )
    return [(row.id, row.worker_pid, row.worker_pgid, row.worker_token) for row in rows]


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
        if not row or row.worker_token != token or row.status not in {"scanning", "orphaned"}:
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


def get_scan_queue_item_by_scan_id(db: Session, scan_id: str) -> ScanQueueItem | None:
    row = db.query(ScanQueueRow).filter(ScanQueueRow.scan_id == scan_id).first()
    return _scan_queue_from_row(row) if row else None


def heartbeat_scan_worker(db: Session, scan_id: str, token: str) -> bool:
    """Record a worker heartbeat only while it still owns the scan."""
    with write_lock(db):
        row = db.get(ScanRow, scan_id)
        if (
            not row
            or row.worker_token != token
            or row.status not in {"scanning", "cancelling", "orphaned"}
            or (row.status == "orphaned" and row.progress_stage == "orphaned")
        ):
            db.rollback()
            return False
        if row.status == "orphaned":
            row.status = "scanning"
            row.progress_message = "Reattached to active scan"
        now = datetime.now(timezone.utc)
        start_scan_attempt(row, now)
        row.worker_heartbeat_at = now
        row.updated_at = now
        db.commit()
        return True


def mark_orphaned_scan_workers(db: Session, active_pids: set[int]) -> list[ScanQueueItem]:
    """Adopt verified worker processes and orphan only workers that have stopped."""
    with write_lock(db):
        rows = (
            db.query(ScanQueueRow)
            .join(ScanRow)
            .filter(ScanRow.status == "scanning")
            .order_by(ScanQueueRow.sort_order, ScanQueueRow.id)
            .all()
        )
        now = datetime.now(timezone.utc)
        for queue_row in rows:
            scan = queue_row.scan
            if scan.worker_pid in active_pids:
                scan.status = "scanning"
                scan.progress_message = "Reattached to active scan"
            else:
                stop_scan_attempt(scan, None)
                scan.status = "orphaned"
                scan.progress_message = "Orphaned scan — worker unavailable; ready to resume"
            scan.updated_at = now
        db.commit()
        return [_scan_queue_from_row(row) for row in rows]


def resume_orphaned_scan(db: Session, queue_id: int) -> bool:
    """Return an orphaned queue item to runnable state by ``queue_id``."""
    with write_lock(db):
        queue_row = db.get(ScanQueueRow, queue_id)
        if not queue_row or queue_row.scan.status != "orphaned":
            db.rollback()
            return False
        scan = queue_row.scan
        scan.status = "queued"
        scan.error_message = None
        scan.progress_stage = "queued"
        scan.progress_message = "Queued to resume from the latest checkpoint"
        scan.worker_pid = None
        scan.worker_pgid = None
        scan.worker_token = None
        scan.worker_heartbeat_at = None
        scan.updated_at = datetime.now(timezone.utc)
        db.commit()
        return True


def scan_worker_process(
    db: Session, queue_id: int
) -> tuple[int | None, int | None, str | None, str] | None:
    """Look up the process identity registered for an active scan worker."""
    queue_row = db.get(ScanQueueRow, queue_id)
    if not queue_row:
        return None
    return (
        queue_row.scan.worker_pid,
        queue_row.scan.worker_pgid,
        queue_row.scan.worker_token,
        queue_row.scan.status,
    )


def clear_scan_worker(db: Session, scan_id: str) -> None:
    row = db.get(ScanRow, scan_id)
    if not row:
        return
    row.worker_pid = None
    row.worker_pgid = None
    row.worker_token = None
    row.worker_heartbeat_at = None
    row.updated_at = datetime.now(timezone.utc)
    db.commit()


def settle_stopped_scan_worker(db: Session, scan_id: str, *, token: str | None) -> None:
    """Settle a stopped attempt without changing a finished or replacement scan."""
    with write_lock(db):
        queue_row = db.query(ScanQueueRow).filter(ScanQueueRow.scan_id == scan_id).first()
        if (
            not queue_row
            or queue_row.scan.worker_token != token
            or queue_row.scan.status not in {"scanning", "cancelling"}
        ):
            db.rollback()
            return
        scan = queue_row.scan
        stop_scan_attempt(scan, None)
        if scan.status == "cancelling":
            scan.status = "cancelled"
            scan.error_message = None
            db.delete(queue_row)
        else:
            scan.status = "orphaned"
            scan.progress_stage = "orphaned"
            scan.progress_message = "Orphaned scan — worker stopped; ready to resume"
            scan.error_message = "Dedicated scan worker is no longer running"
        scan.updated_at = datetime.now(timezone.utc)
        db.commit()


def mark_scan_orphaned(
    db: Session,
    scan_id: str,
    message: str,
    error_message: str | None = None,
    *,
    use_last_heartbeat: bool = False,
) -> None:
    """Mark an interrupted worker as orphaned while retaining resumable work."""
    with write_lock(db):
        row = db.get(ScanRow, scan_id)
        if not row or row.status not in {"scanning", "cancelling", "error", "orphaned"}:
            db.rollback()
            return
        # A worker re-marks its own committed `error` as resumable, but a cancellation
        # that settled that error already removed the queue entry; keep it terminal.
        if row.status == "error" and (
            db.query(ScanQueueRow.id).filter(ScanQueueRow.scan_id == scan_id).first() is None
        ):
            db.rollback()
            return
        now = datetime.now(timezone.utc)
        stop_scan_attempt(row, None if use_last_heartbeat else now)
        row.status = "orphaned"
        row.progress_stage = "orphaned"
        row.progress_message = message
        row.error_message = error_message
        row.updated_at = now
        db.commit()


def reorder_scan_queue(db: Session, queue_ids: list[int], movable: set[int] | None = None) -> None:
    """Order the `movable` jobs (default every job) as given, within the slots they hold.

    Jobs the caller may not see keep their positions, so a caller who sees only
    part of the queue can still order that part.
    """
    rows = db.query(ScanQueueRow).order_by(ScanQueueRow.sort_order, ScanQueueRow.id).all()
    current_ids = {row.id for row in rows}
    chosen = current_ids if movable is None else movable & current_ids
    if len(queue_ids) != len(chosen) or set(queue_ids) != chosen:
        raise ValueError("Provide every queued scan exactly once when reordering")
    rows_by_id = {row.id: row for row in rows}
    given = iter(queue_ids)
    ordered = [next(given) if row.id in chosen else row.id for row in rows]
    for sort_order, queue_id in enumerate(ordered):
        rows_by_id[queue_id].sort_order = sort_order
        rows_by_id[queue_id].updated_at = datetime.now(timezone.utc)
    db.commit()


def cancel_scan_queue_item(db: Session, queue_id: int) -> tuple[str, bool] | None:
    """Cancel a queued scan or mark a running scan for cooperative cancellation.

    A finished scan keeps its terminal status; only its stale queue entry is removed.
    """
    with write_lock(db):
        row = db.get(ScanQueueRow, queue_id)
        if not row:
            db.rollback()
            return None
        scan = row.scan
        now = datetime.now(timezone.utc)
        if scan.status in {"acquiring_model", "scanning", "orphaned", "cancelling"}:
            scan.status = "cancelling"
            scan.updated_at = now
            db.commit()
            return scan.id, True
        if scan.status not in {"complete", "error", "failed", "cancelled"}:
            scan.status = "cancelled"
            scan.updated_at = now
        scan_id = scan.id
        db.delete(row)
        db.commit()
        return scan_id, False


def recover_cancelled_scan_queue_items(db: Session) -> int:
    """Discard cooperative cancellation markers left by a service restart."""
    rows = db.query(ScanQueueRow).join(ScanRow).filter(ScanRow.status == "cancelling").all()
    for row in rows:
        stop_scan_attempt(row.scan, None)
        row.scan.status = "cancelled"
        row.scan.updated_at = datetime.now(timezone.utc)
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)


def recover_interrupted_credits_model_acquisitions(db: Session, scan_id: str | None = None) -> int:
    """Return process-local acquisitions to an explicit, retryable consent state.

    ``scan_id`` limits recovery to one acquisition that failed unexpectedly. The write
    lock keeps a concurrent cancellation from being overwritten.
    """
    with write_lock(db):
        query = (
            db.query(ScanRow)
            .join(ScanQueueRow)
            .filter(
                ScanRow.scan_mode == ScanMode.CREDITS_DETECTION,
                ScanRow.status == "acquiring_model",
            )
        )
        if scan_id is not None:
            query = query.filter(ScanRow.id == scan_id)
        rows = query.all()
        for row in rows:
            row.status = "awaiting_model_consent"
            row.progress_stage = "awaiting_model_consent"
            row.progress_message = "Model acquisition was interrupted; confirm to retry"
            row.updated_at = datetime.now(timezone.utc)
        if rows:
            db.commit()
        else:
            db.rollback()
        return len(rows)


def recover_terminal_scan_queue_items(db: Session) -> int:
    """Remove queue ownership left behind after a terminal scan commit."""
    rows = (
        db.query(ScanQueueRow)
        .join(ScanRow)
        .filter(ScanRow.status.in_(("complete", "error", "failed", "cancelled")))
        .all()
    )
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)


def get_scan_local_path(db: Session, scan_id: str) -> str | None:
    row = db.get(ScanRow, scan_id)
    return row.resolved_local_path if row else None


def is_scan_cancellation_requested(db: Session, scan_id: str) -> bool:
    # The scan worker retains this Session for the full scan. Query the scalar
    # status instead of using ``Session.get`` so a cancellation committed by
    # the API's separate Session is not hidden by the identity map.
    status = db.scalar(select(ScanRow.status).where(ScanRow.id == scan_id))
    return status in {"cancelling", "cancelled"}


def update_scan_status(
    db: Session, scan_id: str, status: str, error_message: str | None = None
) -> None:
    row = db.get(ScanRow, scan_id)
    if not row:
        return
    now = datetime.now(timezone.utc)
    if status not in {"scanning", "cancelling"}:
        stop_scan_attempt(row, now)
    row.status = status
    row.error_message = error_message
    row.updated_at = now
    if status == "complete" and row.origin == "generated":
        from .community_master_filters import refresh_scan_verification

        refresh_scan_verification(db, scan_id)
    db.commit()


def update_scan_duration(db: Session, scan_id: str, duration_seconds: float) -> None:
    row = db.get(ScanRow, scan_id)
    if not row:
        return
    row.duration_seconds = duration_seconds
    row.updated_at = datetime.now(timezone.utc)
    db.commit()


def update_scan_progress(
    db: Session, scan_id: str, update: dict[str, object], *, worker_token: str | None = None
) -> None:
    """Persist stage progress for a scan that still belongs to its worker."""
    row = db.get(ScanRow, scan_id, populate_existing=worker_token is not None)
    if not row:
        return
    if worker_token is not None and (row.status != "scanning" or row.worker_token != worker_token):
        return
    if row.status == "orphaned" and row.progress_stage == "orphaned":
        return
    row.progress_percent = round(float(update.get("percent", 0.0)), 2)
    row.progress_stage = str(update.get("stage", "setup"))
    row.progress_message = str(update.get("message", "Scanning"))
    now = datetime.now(timezone.utc)
    row.worker_heartbeat_at = now
    if row.status == "orphaned":
        start_scan_attempt(row, now)
        row.status = "scanning"
    row.updated_at = now
    db.commit()


def remove_scan_queue_item(db: Session, queue_id: int) -> bool:
    row = db.get(ScanQueueRow, queue_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True
