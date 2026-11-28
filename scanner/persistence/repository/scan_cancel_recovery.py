"""Settle durable scan cancellations after a controller restart or Quit."""

from collections.abc import Callable

from sqlalchemy.orm import Session

from scanner.db import ScanQueueRow, ScanRow


def recover_cancelled_scan_queue_items(
    db: Session,
    *,
    may_be_running: Callable[[str, int | None, int | None, str | None], bool] | None = None,
    on_cancelled: Callable[[str, str | None], None] | None = None,
) -> int:
    """Settle stopped cancellations for the same reservation observed before probing.

    Without a liveness probe, only cancellations without a worker reservation
    can be settled. The callback runs after the matching database commit.
    """
    from .repository_queue import settle_stopped_scan_worker

    attempts = (
        db.query(ScanRow.id, ScanRow.worker_pid, ScanRow.worker_pgid, ScanRow.worker_token)
        .join(ScanQueueRow)
        .filter(ScanRow.status == "cancelling")
        .all()
    )
    recovered = 0
    for scan_id, pid, pgid, token in attempts:
        if may_be_running is None:
            active = (pid, pgid, token) != (None, None, None)
        else:
            active = may_be_running(scan_id, pid, pgid, token)
        if active:
            continue
        settled = settle_stopped_scan_worker(
            db, scan_id, token=token, expected_process=(pid, pgid), expected_status="cancelling"
        )
        if settled == "cancelled":
            recovered += 1
            if on_cancelled is not None:
                on_cancelled(scan_id, token)
    return recovered
