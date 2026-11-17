"""Stored facts that authorize scan jobs, and the terminal state of a refused job."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import MediaItemRow, ScanQueueRow, ScanRow, UserRow
from scanner.models import ScanMode

from .scan_timing import stop_scan_attempt


@dataclass(frozen=True)
class ScanMedia:
    """The file a scan reads, as submission code stored it.

    `server_key` and `item_key` are the `media_items` server and item IDs, and
    `resolved_path` is the scan's own resolved local path, which no route edits.
    """

    server_key: str
    item_key: str
    library_id: str | None
    resolved_path: str


@dataclass(frozen=True)
class ScanJobFacts:
    """Who started one scan, what it reads, and where it stands."""

    scan_id: str
    initiator_id: int | None
    initiator_role: str | None
    media: ScanMedia
    mode: ScanMode
    status: str


def scan_job_facts(db: Session, scan_ids: Iterable[str]) -> dict[str, ScanJobFacts]:
    """Read the authorizing facts of each named scan that exists, in one query."""

    wanted = sorted(set(scan_ids))
    if not wanted:
        return {}
    rows = db.execute(
        select(
            ScanRow.id,
            ScanRow.initiator_id,
            UserRow.role,
            MediaItemRow.server_id,
            MediaItemRow.jellyfin_id,
            MediaItemRow.library_id,
            ScanRow.resolved_local_path,
            ScanRow.scan_mode,
            ScanRow.status,
        )
        .join(MediaItemRow, MediaItemRow.id == ScanRow.media_item_id)
        .outerjoin(UserRow, UserRow.id == ScanRow.initiator_id)
        .where(ScanRow.id.in_(wanted))
    ).all()
    return {
        row.id: ScanJobFacts(
            scan_id=row.id,
            initiator_id=row.initiator_id,
            initiator_role=row.role,
            media=ScanMedia(
                row.server_id, row.jellyfin_id, row.library_id, row.resolved_local_path
            ),
            mode=ScanMode(row.scan_mode),
            status=row.status,
        )
        for row in rows
    }


def queued_scan_id(db: Session, queue_id: int) -> str | None:
    return db.scalar(select(ScanQueueRow.scan_id).where(ScanQueueRow.id == queue_id))


def initiator_stamp(db: Session, scan_id: str) -> tuple[int, str, str] | None:
    """The initiator's permission revision, status, and role, or None without one."""

    row = db.execute(
        select(UserRow.permission_revision, UserRow.status, UserRow.role)
        .join(ScanRow, ScanRow.initiator_id == UserRow.id)
        .where(ScanRow.id == scan_id)
    ).one_or_none()
    return tuple(row) if row is not None else None


def fail_refused_scan_job(db: Session, scan_id: str, message: str) -> None:
    """End a job its initiator may no longer run, so it leaves the queue and blocks nothing."""

    row = db.get(ScanRow, scan_id)
    if row is None:
        return
    now = datetime.now(timezone.utc)
    stop_scan_attempt(row, now)
    row.status = "failed"
    row.error_message = message
    row.progress_stage = "failed"
    row.progress_message = message
    row.worker_pid = None
    row.worker_pgid = None
    row.worker_token = None
    row.worker_heartbeat_at = None
    row.updated_at = now
    db.query(ScanQueueRow).filter(ScanQueueRow.scan_id == scan_id).delete()
    db.commit()
