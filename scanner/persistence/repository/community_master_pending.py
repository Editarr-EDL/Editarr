"""Snapshot downloads the daily sync found but deferred (local API §8).

The sync's revision check records each changed link's newer revision as pending. Pending
downloads are drained after the check and on later page loads, waiting for the service's
`Retry-After` once a download is rate limited, without starting a new daily run.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scanner.db import CommunityMasterLinkRow, CommunitySyncStateRow

from .community_master_links import require_link


def _pending(owner_id: int, service_url: str):
    return (
        CommunityMasterLinkRow.owner_id == owner_id,
        CommunityMasterLinkRow.service_url == service_url,
        CommunityMasterLinkRow.link_state == "active",
        CommunityMasterLinkRow.pending_revision.is_not(None),
    )


def mark_pending_download(db: Session, owner_id: int, link_id: str, revision: int) -> bool:
    """Record that `revision` is newer than the link's latest; False when it is not newer."""
    link = require_link(db, owner_id, link_id)
    if revision <= link.latest_revision:
        return False
    link.pending_revision = max(link.pending_revision or 0, revision)
    db.flush()
    return True


def clear_pending_download(db: Session, owner_id: int, link_id: str) -> None:
    require_link(db, owner_id, link_id).pending_revision = None
    db.flush()


def pending_downloads(db: Session, owner_id: int, service_url: str) -> list[CommunityMasterLinkRow]:
    """The account's active links with a pending download, in a stable order."""
    return list(
        db.scalars(
            select(CommunityMasterLinkRow)
            .where(*_pending(owner_id, service_url))
            .order_by(CommunityMasterLinkRow.id)
        )
    )


def pending_download_count(db: Session, owner_id: int, service_url: str) -> int:
    return db.scalar(select(func.count()).where(*_pending(owner_id, service_url)))


def downloads_ready(db: Session, owner_id: int, service_url: str, now: datetime) -> bool:
    """True unless a rate-limited download asked to wait past `now`."""
    row = db.get(CommunitySyncStateRow, (owner_id, service_url))
    return row is None or row.downloads_retry_at is None or row.downloads_retry_at <= now


def defer_downloads(db: Session, owner_id: int, service_url: str, until: datetime | None) -> None:
    """Hold pending downloads until `until` (None lifts the hold)."""
    row = db.get(CommunitySyncStateRow, (owner_id, service_url))
    if row is None:
        row = CommunitySyncStateRow(owner_id=owner_id, service_url=service_url, state="idle")
        db.add(row)
    row.downloads_retry_at = until
    db.flush()
