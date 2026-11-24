"""Daily master-set sync state and its notifications, per account (local API §8)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityMasterLinkRow,
    CommunitySyncNotificationRow,
    CommunitySyncStateRow,
)

from .community_master_links import utcnow

SYNC_INTERVAL = timedelta(hours=24)
# A run not finished after this long is treated as abandoned so a new one may start.
STALE_RUN = timedelta(hours=1)
# How long a failed run waits when the service gave no `Retry-After`.
FAILED_RETRY = timedelta(hours=1)
NOTIFICATION_LIMIT = 100
NOTIFICATION_KINDS = ("changed", "merged", "unavailable")


@dataclass(frozen=True)
class SyncStatus:
    """The account's sync state with one service, and its newest notifications."""

    state: str
    last_run_at: datetime | None
    next_due_at: datetime | None
    last_error: str | None
    due: bool
    unread_count: int
    notifications: tuple[CommunitySyncNotificationRow, ...]


def _row(db: Session, owner_id: int, service_url: str) -> CommunitySyncStateRow | None:
    return db.get(CommunitySyncStateRow, (owner_id, service_url))


def _running(row: CommunitySyncStateRow, now: datetime) -> bool:
    return (
        row.state == "running"
        and row.last_started_at is not None
        and now - row.last_started_at < STALE_RUN
    )


def sync_due(row: CommunitySyncStateRow | None, now: datetime) -> bool:
    """Due unless the last success was under 24 hours ago, a run is in progress, or a
    failed run's retry time has not come."""
    if row is None:
        return True
    if _running(row, now):
        return False
    if row.state == "failed" and row.next_due_at is not None and now < row.next_due_at:
        return False
    return row.last_success_at is None or now - row.last_success_at >= SYNC_INTERVAL


def sync_status(
    db: Session, owner_id: int, service_url: str, *, now: datetime | None = None
) -> SyncStatus:
    """The state the sync routes report, with at most 100 notifications, newest first."""
    now = now or utcnow()
    row = _row(db, owner_id, service_url)
    return SyncStatus(
        state="idle" if row is None else ("running" if _running(row, now) else row.state),
        last_run_at=None if row is None else row.last_success_at,
        next_due_at=None if row is None else row.next_due_at,
        last_error=None if row is None else row.last_error_code,
        due=sync_due(row, now),
        unread_count=unread_count(db, owner_id),
        notifications=tuple(list_notifications(db, owner_id)),
    )


def begin_sync(
    db: Session,
    owner_id: int,
    service_url: str,
    *,
    contributor_id: str | None,
    now: datetime | None = None,
) -> str | None:
    """Claim a due run and return its `runId`, or None when it is not due."""
    now = now or utcnow()
    row = _row(db, owner_id, service_url)
    if not sync_due(row, now):
        return None
    if row is None:
        row = CommunitySyncStateRow(owner_id=owner_id, service_url=service_url, state="idle")
        db.add(row)
    run_id = str(uuid.uuid4())
    row.contributor_id = contributor_id
    row.state, row.run_id, row.last_started_at = "running", run_id, now
    db.flush()
    return run_id


def finish_sync(
    db: Session,
    owner_id: int,
    service_url: str,
    run_id: str,
    *,
    error_code: str | None = None,
    retry_after: timedelta | None = None,
    now: datetime | None = None,
) -> bool:
    """End the run `run_id`: success sets the next due time 24 hours on; a failure is
    due again after `retry_after` (one hour by default).

    Returns False, changing nothing, when another run has claimed the state since.
    """
    now = now or utcnow()
    row = _row(db, owner_id, service_url)
    if row is None or row.run_id != run_id:
        return False
    if error_code is None:
        row.state, row.last_success_at, row.last_error_code = "idle", now, None
        row.next_due_at = now + SYNC_INTERVAL
    else:
        row.state, row.last_error_code = "failed", error_code
        row.next_due_at = now + (retry_after or FAILED_RETRY)
    db.flush()
    return True


def links_to_sync(db: Session, owner_id: int, service_url: str) -> list[CommunityMasterLinkRow]:
    """The account's active and unavailable links with one service, in a stable order.

    Unavailable links are checked too, so a restored version is followed again.
    """
    return list(
        db.scalars(
            select(CommunityMasterLinkRow)
            .where(
                CommunityMasterLinkRow.owner_id == owner_id,
                CommunityMasterLinkRow.service_url == service_url,
                CommunityMasterLinkRow.link_state.in_(("active", "unavailable")),
            )
            .order_by(CommunityMasterLinkRow.id)
        )
    )


def record_notification(
    db: Session,
    owner_id: int,
    link_id: str,
    kind: str,
    *,
    from_revision: int | None = None,
    to_revision: int | None = None,
    now: datetime | None = None,
) -> CommunitySyncNotificationRow:
    """Record one notification; the same link, kind and target revision is recorded once."""
    if kind not in NOTIFICATION_KINDS:
        raise ValueError(f"Unknown notification kind {kind!r}")
    link = db.get(CommunityMasterLinkRow, link_id)
    if link is None or link.owner_id != owner_id:
        raise ValueError("Notifications belong to the link's account")
    existing = db.scalar(
        select(CommunitySyncNotificationRow).where(
            CommunitySyncNotificationRow.owner_id == owner_id,
            CommunitySyncNotificationRow.link_id == link_id,
            CommunitySyncNotificationRow.kind == kind,
            CommunitySyncNotificationRow.to_revision.is_(None)
            if to_revision is None
            else CommunitySyncNotificationRow.to_revision == to_revision,
        )
    )
    if existing is not None:
        return existing
    row = CommunitySyncNotificationRow(
        id=str(uuid.uuid4()),
        owner_id=owner_id,
        link_id=link_id,
        kind=kind,
        from_revision=from_revision,
        to_revision=to_revision,
        created_at=now or utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def list_notifications(
    db: Session, owner_id: int, limit: int = NOTIFICATION_LIMIT
) -> list[CommunitySyncNotificationRow]:
    """The account's newest notifications, at most 100."""
    return list(
        db.scalars(
            select(CommunitySyncNotificationRow)
            .where(CommunitySyncNotificationRow.owner_id == owner_id)
            .order_by(
                CommunitySyncNotificationRow.created_at.desc(),
                CommunitySyncNotificationRow.id.desc(),
            )
            .limit(min(limit, NOTIFICATION_LIMIT))
        )
    )


def unread_count(db: Session, owner_id: int) -> int:
    return db.scalar(
        select(func.count()).where(
            CommunitySyncNotificationRow.owner_id == owner_id,
            CommunitySyncNotificationRow.read_at.is_(None),
        )
    )


def mark_notifications_read(
    db: Session,
    owner_id: int,
    notification_ids: Iterable[str] | None = None,
    *,
    all_: bool = False,
    now: datetime | None = None,
) -> int:
    """Mark up to 100 of the account's notifications, or all of them, read; return the count."""
    query = select(CommunitySyncNotificationRow).where(
        CommunitySyncNotificationRow.owner_id == owner_id,
        CommunitySyncNotificationRow.read_at.is_(None),
    )
    if not all_:
        ids = list(notification_ids or ())
        if len(ids) > NOTIFICATION_LIMIT:
            raise ValueError(f"At most {NOTIFICATION_LIMIT} notification IDs")
        query = query.where(CommunitySyncNotificationRow.id.in_(ids))
    now = now or utcnow()
    rows = list(db.scalars(query))
    for row in rows:
        row.read_at = now
    db.flush()
    return len(rows)
