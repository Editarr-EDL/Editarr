"""Apply one update-review decision to a filter row (local API §9 decisions)."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityMasterDismissalRow,
    CommunityMasterFilterRow,
    CommunityMasterLinkRow,
    CommunityMasterUpdateRow,
)
from scanner.modeling.community_master_diff import (
    ACCEPT,
    DISMISS,
    KEEP_MINE,
    REMOVED,
    VALUE_FIELDS,
    MasterFilter,
    MasterValues,
)
from scanner.modeling.community_master_state import (
    ADJUSTED,
    LOCAL_COLUMNS,
    REJECTED,
    VERIFIED,
    aligned,
    overrides,
    untouched_state,
)

from .community_master_filters import effective_values, local_values, set_local_values
from .community_master_links import add_filter_row, alignment_of, compact


def _accept(row: CommunityMasterFilterRow, item, values: MasterValues, fields) -> None:
    """Adopt the new master as the baseline and drop overrides of the changed fields."""
    for name in set(fields) & set(VALUE_FIELDS):
        setattr(row, LOCAL_COLUMNS[name], None)
    row.present = item.change != REMOVED and values.present
    if not row.local_touched:
        row.state = untouched_state(values)
    elif row.state == ADJUSTED and not any(
        value is not None for value in local_values(row).values()
    ):
        row.state = VERIFIED


def _keep_mine(
    row: CommunityMasterFilterRow, link: CommunityMasterLinkRow, values: MasterValues
) -> None:
    """Keep the effective values over the new baseline and mark the row touched."""
    alignment = alignment_of(link)
    current = effective_values(row, alignment)
    kept = overrides(current, aligned(values.as_json(), alignment))
    set_local_values(row, kept)
    row.local_touched = True
    if row.state != REJECTED:
        row.state = ADJUSTED if any(value is not None for value in kept.values()) else VERIFIED


def apply_decision(
    db: Session,
    link: CommunityMasterLinkRow,
    item: CommunityMasterUpdateRow,
    decision: str,
    now: datetime,
) -> None:
    """Apply an already validated decision and delete the decided update row."""
    stored = json.loads(item.new_master_json)
    values = MasterValues.from_json(stored)
    if decision == DISMISS:
        dismissal = db.get(CommunityMasterDismissalRow, (link.id, item.filter_id))
        if dismissal is None:
            db.add(
                CommunityMasterDismissalRow(
                    link_id=link.id,
                    filter_id=item.filter_id,
                    values_digest=item.values_digest,
                    dismissed_at=now,
                )
            )
        else:
            dismissal.values_digest, dismissal.dismissed_at = item.values_digest, now
    else:
        row = db.get(CommunityMasterFilterRow, (link.id, item.filter_id))
        if row is None:
            if decision != ACCEPT:
                raise ValueError("Only Accept applies to a new filter")
            filter_ = MasterFilter(item.filter_id, stored["category"], values, stored["info"])
            add_filter_row(db, link.id, filter_, item.to_revision, now)
        else:
            if decision == KEEP_MINE:
                _keep_mine(row, link, values)
            else:
                _accept(row, item, values, json.loads(item.changed_fields_json))
            row.baseline_json = compact(values.as_json())
            row.baseline_revision = item.to_revision
            row.updated_at = now
        db.execute(
            delete(CommunityMasterDismissalRow).where(
                CommunityMasterDismissalRow.link_id == link.id,
                CommunityMasterDismissalRow.filter_id == item.filter_id,
            )
        )
    db.delete(item)
    db.flush()
