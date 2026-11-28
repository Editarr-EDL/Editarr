"""Account for the periods in which a scan has a running worker attempt."""

from datetime import UTC, datetime

from scanner.db import ScanRow
from scanner.models import ScanOrigin


def as_utc(value: datetime) -> datetime:
    """Interpret naïve SQLite timestamps as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _seconds_between(start: datetime, end: datetime) -> float:
    return max(0.0, (as_utc(end) - as_utc(start)).total_seconds())


def start_scan_attempt(row: ScanRow, now: datetime) -> None:
    """Start timing once the worker is confirmed, including on a retry."""
    if row.worker_attempt_started_at is None:
        row.worker_attempt_started_at = now
    if row.worker_started_at is None:
        row.worker_started_at = now


def stop_scan_attempt(row: ScanRow, ended_at: datetime | None) -> None:
    """Bank an attempt once, using its last heartbeat if the stop time is unknown."""
    started_at = row.worker_attempt_started_at
    if started_at is None:
        return
    end = ended_at or row.worker_heartbeat_at or started_at
    row.worker_elapsed_seconds = (row.worker_elapsed_seconds or 0.0) + _seconds_between(
        started_at, end
    )
    row.worker_attempt_started_at = None


def scan_timing(row: ScanRow) -> tuple[float | None, datetime | None]:
    """Return total working time and its sample timestamp if the timer is running."""
    if row.origin != ScanOrigin.GENERATED.value:
        return None, None
    if row.worker_attempt_started_at is None or row.status not in {
        "scanning", "cancelling", "pausing",
    }:
        return row.worker_elapsed_seconds, None
    now = datetime.now(UTC)
    elapsed = _seconds_between(row.worker_attempt_started_at, now)
    return (row.worker_elapsed_seconds or 0.0) + elapsed, now
