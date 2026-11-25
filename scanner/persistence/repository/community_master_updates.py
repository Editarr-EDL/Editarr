"""Store newer master revisions and review their changes (local API §8 check, §9).

A newer snapshot never changes what is in effect: it refreshes informational values,
re-keys merged rows, and rebuilds the undecided update rows. Decisions apply them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityMasterDismissalRow,
    CommunityMasterFilterRow,
    CommunityMasterLinkRow,
    CommunityMasterUpdateRow,
)
from scanner.modeling.community_master_diff import (
    ACCEPT,
    CHANGES,
    DISMISS,
    GROUPS,
    BaselineRow,
    MasterValues,
    allowed_decisions,
    can_accept,
    diff_master,
)
from scanner.modeling.community_master_state import aligned

from .community_master_decisions import apply_decision
from .community_master_filters import baseline_of, effective_values
from .community_master_links import (
    CommunityMasterError,
    VerifiedSnapshot,
    alignment_of,
    compact,
    is_fuzzy,
    prune_snapshots,
    require_link,
    store_snapshot,
    utcnow,
)

MAX_DECISIONS = 500


@dataclass(frozen=True)
class UpdateRowView:
    """One reviewable row; master dicts are on the local timeline."""

    row_id: str
    filter_id: str
    category: str
    group: str
    touched: bool
    changed_fields: tuple[str, ...]
    mine: dict | None
    old_master: dict | None
    new_master: dict | None
    merged_into: str | None
    allowed: tuple[str, ...]
    can_accept: bool


@dataclass(frozen=True)
class UpdateView:
    from_revision: int
    to_revision: int
    alignment_required: bool
    alignment_approved: bool
    groups: dict[str, list[UpdateRowView]]


def pending_counts(db: Session, link_id: str) -> dict:
    """Undecided rows per API group: `new`, `changed`, `removed`, `nowLowConfidence`."""
    counts = dict(
        db.execute(
            select(CommunityMasterUpdateRow.change, func.count())
            .where(CommunityMasterUpdateRow.link_id == link_id)
            .group_by(CommunityMasterUpdateRow.change)
        ).all()
    )
    return {GROUPS[change]: counts.get(change, 0) for change in CHANGES}


def _rekey(db: Session, link_id: str, rekeys: dict) -> None:
    """Move re-keyed rows and their dismissals to the merged filter's ID."""
    db.execute(
        delete(CommunityMasterDismissalRow).where(
            CommunityMasterDismissalRow.link_id == link_id,
            CommunityMasterDismissalRow.filter_id.in_(list(rekeys.values())),
        )
    )
    for table in (CommunityMasterFilterRow, CommunityMasterDismissalRow):
        for old, new in rekeys.items():
            db.execute(
                update(table)
                .where(table.link_id == link_id, table.filter_id == old)
                .values(filter_id=new)
            )
    db.expire_all()


def store_latest_snapshot(
    db: Session,
    owner_id: int,
    link_id: str,
    snapshot: VerifiedSnapshot,
    *,
    now: datetime | None = None,
) -> bool:
    """Store a newer verified revision and rebuild its update rows; never apply them.

    Returns whether the revision was newer. Informational values refresh at once;
    when no material change remains, `appliedRevision` advances silently.
    """
    now = now or utcnow()
    link = require_link(db, owner_id, link_id)
    if snapshot.revision <= link.latest_revision:
        if snapshot.revision == link.latest_revision:
            store_snapshot(db, link, snapshot, now)
        return False
    store_snapshot(db, link, snapshot, now)
    rows = list(
        db.scalars(
            select(CommunityMasterFilterRow).where(CommunityMasterFilterRow.link_id == link.id)
        )
    )
    dismissed = dict(
        db.execute(
            select(
                CommunityMasterDismissalRow.filter_id, CommunityMasterDismissalRow.values_digest
            ).where(CommunityMasterDismissalRow.link_id == link.id)
        ).all()
    )
    diff = diff_master(
        (
            BaselineRow(row.filter_id, row.category, baseline_of(row), bool(row.local_touched))
            for row in rows
        ),
        snapshot.filters,
        dismissed,
    )
    _rekey(db, link.id, dict(diff.rekeys))
    pending = {row.filter_id for row in diff.rows} | set(diff.kept_dismissals)
    for row in db.scalars(
        select(CommunityMasterFilterRow).where(CommunityMasterFilterRow.link_id == link.id)
    ):
        if row.filter_id in diff.info:
            row.info_json = compact(dict(diff.info[row.filter_id]))
            if row.filter_id not in pending:
                row.baseline_revision = snapshot.revision
            row.updated_at = now
    db.execute(delete(CommunityMasterUpdateRow).where(CommunityMasterUpdateRow.link_id == link.id))
    db.execute(
        delete(CommunityMasterDismissalRow).where(
            CommunityMasterDismissalRow.link_id == link.id,
            CommunityMasterDismissalRow.filter_id.not_in(diff.kept_dismissals),
        )
    )
    for item in diff.rows:
        db.add(
            CommunityMasterUpdateRow(
                link_id=link.id,
                filter_id=item.filter_id,
                to_revision=snapshot.revision,
                change=item.change,
                changed_fields_json=json.dumps(list(item.changed_fields)),
                new_master_json=compact(
                    {
                        "category": item.category,
                        "info": dict(item.info),
                        **item.new_values.as_json(),
                    }
                ),
                merged_into=item.merged_into,
                values_digest=item.digest,
            )
        )
    link.latest_revision = snapshot.revision
    if link.pending_revision is not None and link.pending_revision <= snapshot.revision:
        link.pending_revision = None
    link.updated_at = now
    db.flush()
    _finish_if_decided(db, link)
    return True


def _finish_if_decided(db: Session, link: CommunityMasterLinkRow) -> None:
    """Advance `appliedRevision` once no undecided row remains, then drop unused snapshots."""
    remaining = db.scalar(select(func.count()).where(CommunityMasterUpdateRow.link_id == link.id))
    if not remaining and link.applied_revision != link.latest_revision:
        link.applied_revision = link.latest_revision
    db.flush()
    prune_snapshots(db, link)


def _aligned_for(link: CommunityMasterLinkRow, revision: int) -> bool:
    return link.alignment_state == "approved" and link.alignment_revision == revision


def _row_view(
    db: Session, link: CommunityMasterLinkRow, item: CommunityMasterUpdateRow
) -> UpdateRowView:
    alignment = alignment_of(link)
    row = db.get(CommunityMasterFilterRow, (link.id, item.filter_id))
    touched = bool(row is not None and row.local_touched)
    stored = json.loads(item.new_master_json)
    new_master = None
    if item.change != "removed":
        new_master = {
            **stored["info"],
            **aligned(MasterValues.from_json(stored).as_json(), alignment),
        }
    return UpdateRowView(
        row_id=item.filter_id,
        filter_id=item.filter_id,
        category=stored["category"],
        group=GROUPS[item.change],
        touched=touched,
        changed_fields=tuple(json.loads(item.changed_fields_json)),
        mine=effective_values(row, alignment) if touched else None,
        old_master=None if row is None else aligned(baseline_of(row).as_json(), alignment),
        new_master=new_master,
        merged_into=item.merged_into,
        allowed=allowed_decisions(item.change, touched),
        can_accept=can_accept(
            item.change,
            fuzzy=is_fuzzy(link),
            aligned_for_revision=_aligned_for(link, item.to_revision),
        ),
    )


def master_update(db: Session, owner_id: int, link_id: str) -> UpdateView:
    """The undecided rows of the latest revision, grouped as the API returns them."""
    link = require_link(db, owner_id, link_id)
    groups: dict[str, list[UpdateRowView]] = {GROUPS[change]: [] for change in CHANGES}
    items = db.scalars(
        select(CommunityMasterUpdateRow)
        .where(CommunityMasterUpdateRow.link_id == link.id)
        .order_by(CommunityMasterUpdateRow.filter_id)
    )
    for item in items:
        view = _row_view(db, link, item)
        groups[view.group].append(view)
    return UpdateView(
        from_revision=link.applied_revision,
        to_revision=link.latest_revision,
        alignment_required=is_fuzzy(link),
        alignment_approved=_aligned_for(link, link.latest_revision),
        groups=groups,
    )


def decide_updates(
    db: Session,
    owner_id: int,
    link_id: str,
    to_revision: int,
    decisions: Iterable[tuple[str, str]],
    *,
    now: datetime | None = None,
) -> UpdateView:
    """Apply `(row_id, decision)` pairs atomically: all are checked before any applies.

    A stale `to_revision` or an already decided row is `update_changed`; a decision
    outside the row's `allowed` list, or Accept while it is disabled, is
    `decision_not_allowed`.
    """
    decisions = list(decisions)
    if len(decisions) > MAX_DECISIONS:
        raise ValueError(f"At most {MAX_DECISIONS} decisions per request")
    return _decide(db, owner_id, link_id, to_revision, decisions, now or utcnow())


def _decide(
    db: Session,
    owner_id: int,
    link_id: str,
    to_revision: int,
    decisions: list[tuple[str, str]],
    now: datetime,
) -> UpdateView:
    """Check every decision, then apply them all in the caller's transaction."""
    link = require_link(db, owner_id, link_id)
    if to_revision != link.latest_revision:
        raise CommunityMasterError("update_changed")
    planned = []
    for row_id, decision in decisions:
        item = db.get(CommunityMasterUpdateRow, (link.id, row_id))
        if item is None or item.to_revision != to_revision:
            raise CommunityMasterError("update_changed")
        view = _row_view(db, link, item)
        if decision not in view.allowed or (decision == ACCEPT and not view.can_accept):
            raise CommunityMasterError("decision_not_allowed")
        planned.append((item, decision))
    if len({item.filter_id for item, _decision in planned}) != len(planned):
        raise CommunityMasterError("decision_not_allowed")
    for item, decision in planned:
        apply_decision(db, link, item, decision, now)
    _finish_if_decided(db, link)
    from .community_master_filters import refresh_local_verification

    refresh_local_verification(db, link)
    return master_update(db, owner_id, link_id)


def accept_all(
    db: Session,
    owner_id: int,
    link_id: str,
    to_revision: int,
    group: str,
    *,
    now: datetime | None = None,
) -> UpdateView:
    """Accept every untouched row of an API group whose Accept is enabled.

    The per-request decision limit does not apply; all rows apply in one transaction.
    """
    if group not in GROUPS.values():
        raise ValueError(f"Unknown group {group!r}")
    view = master_update(db, owner_id, link_id)
    if to_revision != view.to_revision:
        raise CommunityMasterError("update_changed")
    chosen = [
        (row.row_id, ACCEPT) for row in view.groups[group] if not row.touched and row.can_accept
    ]
    return _decide(db, owner_id, link_id, to_revision, chosen, now or utcnow())


__all__ = [
    "ACCEPT",
    "DISMISS",
    "MAX_DECISIONS",
    "UpdateRowView",
    "UpdateView",
    "accept_all",
    "decide_updates",
    "master_update",
    "pending_counts",
    "store_latest_snapshot",
]
