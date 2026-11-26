"""Follow newer master revisions (local API §8 check and daily sync, ADR 0002 U3).

A link always shows its latest verified snapshot: storing a newer revision applies it
at once. Rows keep their filter IDs (a merged filter re-keys the row it continues),
take the new master values, and rows the master no longer serves leave the set.
Reviews that include the link keep their own snapshot until the user refreshes it.

Earlier builds kept local decisions (`state`, `local_*`), undecided update rows and
dismissals; they stay stored but unused, and a link left behind its latest revision
catches up on its next check or sync.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from scanner.db import CommunityMasterFilterRow, CommunityMasterLinkRow
from scanner.modeling.community_master_diff import MasterFilter, master_filters, rekeys

from .community_master_links import (
    VerifiedSnapshot,
    add_filter_row,
    compact,
    latest_snapshot,
    prune_snapshots,
    require_link,
    store_snapshot,
    utcnow,
)


def _rekey(db: Session, link_id: str, moves: dict[str, str]) -> None:
    """Move re-keyed rows to the merged filter's ID."""
    for old, new in moves.items():
        db.execute(
            update(CommunityMasterFilterRow)
            .where(
                CommunityMasterFilterRow.link_id == link_id,
                CommunityMasterFilterRow.filter_id == old,
            )
            .values(filter_id=new)
        )
    db.expire_all()


def apply_filters(
    db: Session,
    link: CommunityMasterLinkRow,
    filters: Iterable[MasterFilter],
    revision: int,
    now: datetime,
) -> None:
    """Make the link's rows equal a verified snapshot's served filters."""
    filters = tuple(filters)
    rows = db.scalars(
        select(CommunityMasterFilterRow.filter_id).where(
            CommunityMasterFilterRow.link_id == link.id
        )
    )
    _rekey(db, link.id, rekeys(rows, filters))
    existing = {
        row.filter_id: row
        for row in db.scalars(
            select(CommunityMasterFilterRow).where(CommunityMasterFilterRow.link_id == link.id)
        )
    }
    served = set()
    for item in filters:
        served.add(item.filter_id)
        row = existing.get(item.filter_id)
        if row is None:
            add_filter_row(db, link.id, item, revision, now)
            continue
        row.category = item.category
        row.baseline_json = compact(item.values.as_json())
        row.baseline_revision = revision
        row.info_json = compact(dict(item.info))
        row.present = True
        row.updated_at = now
    for filter_id, row in existing.items():
        if filter_id not in served and row.present:
            values = json.loads(row.baseline_json)
            row.baseline_json = compact({**values, "present": False})
            row.baseline_revision = revision
            row.present = False
            row.updated_at = now
    link.latest_revision = max(link.latest_revision, revision)
    link.applied_revision = revision
    db.flush()


def _finish(db: Session, link: CommunityMasterLinkRow) -> None:
    from .community_master_filters import refresh_local_verification

    prune_snapshots(db, link)
    refresh_local_verification(db, link)


def catch_up_link(db: Session, link: CommunityMasterLinkRow, now: datetime | None = None) -> bool:
    """Apply the stored latest snapshot to a link an earlier build left behind it."""
    if link.applied_revision == link.latest_revision:
        return False
    snapshot = latest_snapshot(db, link)
    if snapshot is None:
        return False
    filters = master_filters(json.loads(snapshot.snapshot_bytes))
    apply_filters(db, link, filters, link.latest_revision, now or utcnow())
    _finish(db, link)
    return True


def store_latest_snapshot(
    db: Session,
    owner_id: int,
    link_id: str,
    snapshot: VerifiedSnapshot,
    *,
    now: datetime | None = None,
) -> bool:
    """Store a newer verified revision and apply it; returns whether it was newer."""
    now = now or utcnow()
    link = require_link(db, owner_id, link_id)
    if snapshot.revision <= link.latest_revision:
        if snapshot.revision == link.latest_revision:
            store_snapshot(db, link, snapshot, now)
        catch_up_link(db, link, now)
        return False
    store_snapshot(db, link, snapshot, now)
    apply_filters(db, link, snapshot.filters, snapshot.revision, now)
    link.latest_revision = snapshot.revision
    if link.pending_revision is not None and link.pending_revision <= snapshot.revision:
        link.pending_revision = None
    link.updated_at = now
    db.flush()
    _finish(db, link)
    return True


__all__ = ["apply_filters", "catch_up_link", "store_latest_snapshot"]
