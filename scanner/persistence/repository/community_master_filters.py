"""Master view and per-filter local decisions for one account (local API §3, §5)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityMasterFilterRow,
    CommunityMasterLinkRow,
    CommunityMasterPreferenceRow,
    ScanFindingRow,
    ScanMediaVersionRow,
    ScanRow,
)
from scanner.modeling.community_master_diff import VALUE_FIELDS, MasterValues
from scanner.modeling.community_master_state import (
    ADJUSTED,
    LOCAL_COLUMNS,
    REJECTED,
    VERIFIED,
    Preferences,
    aligned,
    effective,
    intervals_overlap,
    is_active,
    overrides,
    untouched_state,
)

from .community_master_links import (
    CommunityMasterError,
    alignment_of,
    require_link,
    utcnow,
)

DECISIONS = ("verify", "reject", "adjust", "reset")
VOTES = ("up", "down", "none")


@dataclass(frozen=True)
class MasterFilterView:
    """One row of the master view; value dicts use the API's camelCase field names."""

    filter_id: str
    category: str
    master: dict
    local: dict | None
    effective: dict
    state: str
    touched: bool
    locally_verified: bool
    my_vote: str | None
    active: bool


@dataclass(frozen=True)
class MasterView:
    link: CommunityMasterLinkRow
    preferences: Preferences
    filters: tuple[MasterFilterView, ...]
    pending_update: dict


def baseline_of(row: CommunityMasterFilterRow) -> MasterValues:
    return MasterValues.from_json(json.loads(row.baseline_json))


def local_values(row: CommunityMasterFilterRow) -> dict:
    return {name: getattr(row, column) for name, column in LOCAL_COLUMNS.items()}


def set_local_values(row: CommunityMasterFilterRow, values: Mapping) -> None:
    for name, column in LOCAL_COLUMNS.items():
        setattr(row, column, values.get(name))


def master_values(row: CommunityMasterFilterRow, alignment: Mapping | None) -> dict:
    """The baseline master values on the local timeline."""
    return aligned(baseline_of(row).as_json(), alignment)


def effective_values(row: CommunityMasterFilterRow, alignment: Mapping | None) -> dict:
    return effective(master_values(row, alignment), local_values(row))


def get_preferences(db: Session, owner_id: int) -> Preferences:
    """The account's preferences, or the defaults (0.0, False, False)."""
    row = db.get(CommunityMasterPreferenceRow, owner_id)
    if row is None:
        return Preferences()
    return Preferences(
        row.confidence_threshold,
        bool(row.show_low_confidence),
        bool(row.hide_rejected_downvoted),
    )


def set_preferences(
    db: Session,
    owner_id: int,
    *,
    confidence_threshold: float | None = None,
    show_low_confidence: bool | None = None,
    hide_rejected_downvoted: bool | None = None,
) -> Preferences:
    """Update any preference left non-None; the threshold must be within 0..1."""
    if confidence_threshold is not None and not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("confidence_threshold must be between 0 and 1")
    row = db.get(CommunityMasterPreferenceRow, owner_id)
    if row is None:
        row = CommunityMasterPreferenceRow(
            owner_id=owner_id,
            confidence_threshold=0.0,
            show_low_confidence=False,
            hide_rejected_downvoted=False,
        )
        db.add(row)
    if confidence_threshold is not None:
        row.confidence_threshold = float(confidence_threshold)
    if show_low_confidence is not None:
        row.show_low_confidence = bool(show_low_confidence)
    if hide_rejected_downvoted is not None:
        row.hide_rejected_downvoted = bool(hide_rejected_downvoted)
    db.flush()
    return get_preferences(db, owner_id)


def filter_view(
    row: CommunityMasterFilterRow, link: CommunityMasterLinkRow, preferences: Preferences
) -> MasterFilterView:
    """Project one stored row into the master view."""
    alignment = alignment_of(link)
    baseline = baseline_of(row)
    info = json.loads(row.info_json)
    master = {**info, **master_values(row, alignment)}
    local = local_values(row)
    current = effective(master, local)
    return MasterFilterView(
        filter_id=row.filter_id,
        category=row.category,
        master=master,
        local=current if any(value is not None for value in local.values()) else None,
        effective=current,
        state=row.state,
        touched=bool(row.local_touched),
        locally_verified=bool(row.locally_verified),
        my_vote=row.my_vote,
        active=is_active(
            state=row.state,
            touched=bool(row.local_touched),
            present=bool(row.present),
            low_confidence=baseline.low_confidence,
            confidence=info.get("confidence"),
            alignment_state=link.alignment_state,
            preferences=preferences,
        ),
    )


def master_view(db: Session, owner_id: int, link_id: str) -> MasterView:
    """The link with every row in the local set, in master start order."""
    from .community_master_updates import pending_counts

    link = require_link(db, owner_id, link_id)
    preferences = get_preferences(db, owner_id)
    rows = db.scalars(
        select(CommunityMasterFilterRow).where(
            CommunityMasterFilterRow.link_id == link.id, CommunityMasterFilterRow.present
        )
    )
    views = sorted(
        (filter_view(row, link, preferences) for row in rows),
        key=lambda view: (view.master["startMs"], view.master["endMs"], view.filter_id),
    )
    return MasterView(link, preferences, tuple(views), pending_counts(db, link.id))


def _require_row(db: Session, owner_id: int, link_id: str, filter_id: str):
    link = require_link(db, owner_id, link_id)
    row = db.get(CommunityMasterFilterRow, (link.id, filter_id))
    if row is None:
        raise CommunityMasterError("not_found")
    return link, row


def set_filter_decision(
    db: Session,
    owner_id: int,
    link_id: str,
    filter_id: str,
    decision: str,
    values: Mapping | None = None,
    *,
    expected_state: str | None = None,
    now: datetime | None = None,
) -> MasterFilterView:
    """Verify, reject, adjust or reset one row (§5).

    `values` holds camelCase value fields for `adjust`, validated by the caller against
    the taxonomy; here the effective bounds must satisfy `0 <= startMs < endMs`.
    Verify, reject and adjust mark the row touched; reset clears the local values and
    touched, unless the user voted. A stale `expected_state` is `filter_state_changed`.
    """
    if decision not in DECISIONS:
        raise ValueError(f"Unknown decision {decision!r}")
    link, row = _require_row(db, owner_id, link_id, filter_id)
    if expected_state is not None and row.state != expected_state:
        raise CommunityMasterError("filter_state_changed")
    alignment = alignment_of(link)
    if decision == "adjust":
        changes = {name: value for name, value in (values or {}).items() if value is not None}
        if not changes or set(changes) - set(VALUE_FIELDS):
            raise ValueError("adjust requires at least one value field")
        target = {**effective_values(row, alignment), **changes}
        if not 0 <= target["startMs"] < target["endMs"]:
            raise ValueError("Bounds must satisfy 0 <= start_ms < end_ms")
        set_local_values(row, overrides(target, master_values(row, alignment)))
        row.state, row.local_touched = ADJUSTED, True
    elif decision == "reset":
        set_local_values(row, {})
        row.state = untouched_state(baseline_of(row))
        row.local_touched = row.my_vote is not None
    else:
        row.state, row.local_touched = (VERIFIED if decision == "verify" else REJECTED), True
    row.updated_at = now or utcnow()
    db.flush()
    refresh_local_verification(db, link)
    return filter_view(row, link, get_preferences(db, owner_id))


def set_filter_vote(
    db: Session,
    owner_id: int,
    link_id: str,
    filter_id: str,
    vote: str,
    now: datetime | None = None,
) -> MasterFilterView:
    """Record the account's acknowledged vote; any vote but `none` marks the row touched."""
    if vote not in VOTES:
        raise ValueError(f"Unknown vote {vote!r}")
    link, row = _require_row(db, owner_id, link_id, filter_id)
    row.my_vote = None if vote == "none" else vote
    if vote != "none":
        row.local_touched = True
    row.updated_at = now or utcnow()
    db.flush()
    return filter_view(row, link, get_preferences(db, owner_id))


def refresh_local_verification(db: Session, link: CommunityMasterLinkRow) -> None:
    """Set `locally_verified` for verified and adjusted rows, and for rows that overlap
    a local model finding of the same category on the link's local version.

    Overlap counts only once master bounds are on the local timeline, so a fuzzy link
    still awaiting alignment is verified only by the user's own decisions (D21).
    """
    findings = db.execute(
        select(ScanFindingRow.category, ScanFindingRow.start_seconds, ScanFindingRow.end_seconds)
        .join(ScanRow, ScanRow.id == ScanFindingRow.scan_id)
        .join(ScanMediaVersionRow, ScanMediaVersionRow.scan_id == ScanRow.id)
        .where(
            ScanMediaVersionRow.version_id == link.media_version_id,
            ScanRow.origin == "generated",
            ScanFindingRow.category.is_not(None),
        )
    ).all()
    by_category: dict[str, list] = {}
    for category, start, end in findings:
        by_category.setdefault(getattr(category, "value", category), []).append((start, end))
    if link.alignment_state == "required":
        by_category = {}
    alignment = alignment_of(link)
    rows = db.scalars(
        select(CommunityMasterFilterRow).where(CommunityMasterFilterRow.link_id == link.id)
    )
    for row in rows:
        current = effective_values(row, alignment)
        overlap = any(
            intervals_overlap(current["startMs"], current["endMs"], start, end)
            for start, end in by_category.get(row.category, ())
        )
        verified = row.state in (VERIFIED, ADJUSTED) or overlap
        if bool(row.locally_verified) != verified:
            row.locally_verified = verified
    db.flush()


def refresh_version_verification(db: Session, media_version_id: str) -> int:
    """Refresh `locally_verified` on every live link of a local version, for any account.

    Call it when local model findings change, such as when a scan of the version
    completes. Returns the number of links refreshed.
    """
    links = list(
        db.scalars(
            select(CommunityMasterLinkRow).where(
                CommunityMasterLinkRow.media_version_id == media_version_id,
                CommunityMasterLinkRow.link_state != "replaced",
            )
        )
    )
    for link in links:
        refresh_local_verification(db, link)
    return len(links)


def refresh_scan_verification(db: Session, scan_id: str) -> int:
    """Refresh the links of the local version a scan is bound to; see
    `refresh_version_verification`."""
    version_id = db.scalar(
        select(ScanMediaVersionRow.version_id).where(ScanMediaVersionRow.scan_id == scan_id)
    )
    return 0 if version_id is None else refresh_version_verification(db, version_id)
