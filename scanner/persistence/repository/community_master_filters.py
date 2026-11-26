"""Master view, votes and display preferences for one account (local API §3, §5).

The master view is read-only for filter decisions (ADR 0002 U1): filters are changed in
the reviews that include them. Older local decision columns stay in the database unused.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
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
from scanner.modeling.community_master_diff import MasterValues
from scanner.modeling.community_master_state import Preferences, aligned, intervals_overlap
from scanner.modeling.community_review_filters import starts_enabled

from .community_master_links import (
    CommunityMasterError,
    alignment_of,
    require_link,
    utcnow,
)

VOTES = ("up", "down", "none")


@dataclass(frozen=True)
class MasterFilterView:
    """One row of the master view; `master` uses the API's camelCase field names.

    `active` is whether the filter starts enabled when a review includes it.
    """

    filter_id: str
    category: str
    master: dict
    locally_verified: bool
    my_vote: str | None
    active: bool


@dataclass(frozen=True)
class MasterView:
    """The link, preferences and served rows; `retired_votes` maps filters the link no
    longer serves to the account's last acknowledged vote on them."""

    link: CommunityMasterLinkRow
    preferences: Preferences
    filters: tuple[MasterFilterView, ...]
    retired_votes: Mapping[str, str] = field(default_factory=dict)


def baseline_of(row: CommunityMasterFilterRow) -> MasterValues:
    return MasterValues.from_json(json.loads(row.baseline_json))


def master_values(row: CommunityMasterFilterRow, alignment: Mapping | None) -> dict:
    """The row's master values (from the link's latest snapshot) on the local timeline."""
    return aligned(baseline_of(row).as_json(), alignment)


def get_preferences(db: Session, owner_id: int) -> Preferences:
    """The account's preferences, or the defaults (0.0, False, False)."""
    row = db.get(CommunityMasterPreferenceRow, owner_id)
    if row is None:
        return Preferences()
    return Preferences(
        row.confidence_threshold,
        bool(row.show_low_confidence),
        bool(row.hide_downvoted),
    )


def set_preferences(
    db: Session,
    owner_id: int,
    *,
    confidence_threshold: float | None = None,
    show_low_confidence: bool | None = None,
    hide_downvoted: bool | None = None,
) -> Preferences:
    """Update any preference left non-None; the threshold must be within 0..1.

    The threshold and Show Low Confidence apply to reviews when they next include or
    refresh community filters; reviews already holding filters are unchanged.
    """
    if confidence_threshold is not None and not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("confidence_threshold must be between 0 and 1")
    row = db.get(CommunityMasterPreferenceRow, owner_id)
    if row is None:
        row = CommunityMasterPreferenceRow(
            owner_id=owner_id,
            confidence_threshold=0.0,
            show_low_confidence=False,
            hide_downvoted=False,
        )
        db.add(row)
    if confidence_threshold is not None:
        row.confidence_threshold = float(confidence_threshold)
    if show_low_confidence is not None:
        row.show_low_confidence = bool(show_low_confidence)
    if hide_downvoted is not None:
        row.hide_downvoted = bool(hide_downvoted)
    db.flush()
    return get_preferences(db, owner_id)


def filter_view(
    row: CommunityMasterFilterRow, link: CommunityMasterLinkRow, preferences: Preferences
) -> MasterFilterView:
    """Project one stored row into the master view."""
    baseline = baseline_of(row)
    info = json.loads(row.info_json)
    return MasterFilterView(
        filter_id=row.filter_id,
        category=row.category,
        master={**info, **master_values(row, alignment_of(link))},
        locally_verified=bool(row.locally_verified),
        my_vote=row.my_vote,
        active=link.alignment_state != "required"
        and bool(row.present)
        and starts_enabled(
            low_confidence=baseline.low_confidence,
            confidence=info.get("confidence"),
            threshold=preferences.confidence_threshold,
            show_low_confidence=preferences.show_low_confidence,
        ),
    )


def master_view(db: Session, owner_id: int, link_id: str) -> MasterView:
    """The link with every served row, in master start order, and the account's votes
    on rows it no longer serves."""
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
    retired = db.execute(
        select(CommunityMasterFilterRow.filter_id, CommunityMasterFilterRow.my_vote).where(
            CommunityMasterFilterRow.link_id == link.id,
            CommunityMasterFilterRow.present.is_(False),
            CommunityMasterFilterRow.my_vote.is_not(None),
        )
    ).all()
    return MasterView(link, preferences, tuple(views), dict(retired))


def _require_row(db: Session, owner_id: int, link_id: str, filter_id: str):
    link = require_link(db, owner_id, link_id)
    row = db.get(CommunityMasterFilterRow, (link.id, filter_id))
    if row is None:
        raise CommunityMasterError("not_found")
    return link, row


def set_filter_vote(
    db: Session,
    owner_id: int,
    link_id: str,
    filter_id: str,
    vote: str,
    now: datetime | None = None,
) -> MasterFilterView:
    """Record the account's acknowledged vote."""
    if vote not in VOTES:
        raise ValueError(f"Unknown vote {vote!r}")
    link, row = _require_row(db, owner_id, link_id, filter_id)
    row.my_vote = None if vote == "none" else vote
    row.updated_at = now or utcnow()
    db.flush()
    return filter_view(row, link, get_preferences(db, owner_id))


def refresh_local_verification(db: Session, link: CommunityMasterLinkRow) -> None:
    """Set `locally_verified` for rows whose master bounds overlap a local model finding
    of the same top-level category on the link's local version (ADR 0002 U7).

    Overlap counts only once master bounds are on the local timeline, so no row of a
    fuzzy link still awaiting alignment is verified.
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
        current = master_values(row, alignment)
        verified = any(
            intervals_overlap(current["startMs"], current["endMs"], start, end)
            for start, end in by_category.get(row.category, ())
        )
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
