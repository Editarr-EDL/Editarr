"""Community filter snapshots in saved reviews (local API §3, review membership).

Including a master link in a review stores a snapshot of the link's served filters
(master values on the local timeline, after approved alignment) and adds each one to
the review as a normal review filter (`review_community_filters`). Later changes to the
link never touch the review; the review reports them as `changed`, and the owner
refreshes the snapshot by saving the review, which advances its revisions like any
other content change.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityMasterLinkRow,
    MediaVersionLocatorRow,
    ReviewCommunitySourceRow,
    ReviewMediaVersionRow,
    SavedReviewRow,
)
from scanner.modeling.community_review_filters import served, snapshot_digest
from scanner.models import ReviewCommunitySource

from .community_master_links import alignment_of, utcnow
from .review_community_drafts import validate_draft
from .review_community_filters import SourcePlan, entries_of, link_entries

MAX_SOURCES = 32


def _membership_error(message: str) -> ValueError:
    from scanner.repository import ReviewMembershipError

    return ReviewMembershipError(message)


# The label of an unlabeled candidate version, as in the master view.
UNLABELED = "Other"


def link_usable(link: CommunityMasterLinkRow | None, owner_id: int | None) -> bool:
    """Whether a snapshot can be (re)taken from `link` for a review that `owner_id` owns."""
    return link is not None and link.owner_id == owner_id and link.link_state != "replaced"


def capture(
    db: Session, link: CommunityMasterLinkRow, row: ReviewCommunitySourceRow | None = None
) -> ReviewCommunitySourceRow:
    """Take a snapshot of the link's served filters into `row`, or a new row."""
    entries = link_entries(db, link)
    alignment = alignment_of(link)
    row = row or ReviewCommunitySourceRow(id=str(uuid.uuid4()))
    row.link_id = link.id
    row.link = link
    row.community_version_id = link.community_version_id
    row.label = link.label or UNLABELED
    row.tier = link.tier
    row.match_percent = link.match_percent
    row.master_revision = link.applied_revision
    row.alignment_json = json.dumps(alignment, sort_keys=True) if alignment else None
    row.filters_json = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    row.filters_digest = snapshot_digest(entries)
    row.captured_at = utcnow()
    return row


def source_state(db: Session, review: SavedReviewRow, row: ReviewCommunitySourceRow) -> str:
    """How a stored snapshot compares with its link's served filters now."""
    link = row.link
    if not link_usable(link, review.owner_id):
        return "link_unavailable"
    if link.alignment_state == "required":
        return "alignment_required"
    current = snapshot_digest(link_entries(db, link)) == row.filters_digest
    return "current" if current else "changed"


def review_sources(db: Session, review: SavedReviewRow) -> list[ReviewCommunitySource]:
    """Each stored snapshot and its state against the link now."""
    return [
        ReviewCommunitySource(
            source_id=row.id,
            link_id=row.link_id,
            community_version_id=row.community_version_id,
            label=row.label,
            tier=row.tier,
            match_percent=row.match_percent,
            master_revision=row.master_revision,
            alignment=json.loads(row.alignment_json) if row.alignment_json else None,
            captured_at=row.captured_at.isoformat(),
            state=source_state(db, review, row),
            link_state=row.link.link_state if row.link is not None else None,
            filter_count=sum(1 for entry in entries_of(row) if served(entry)),
        )
        for row in review.community_sources
    ]


def review_version_ids(db: Session, review: SavedReviewRow) -> set[str]:
    """The local versions of the review's catalog target and bound Editarr version."""
    versions = set(
        db.scalars(
            select(MediaVersionLocatorRow.version_id).where(
                MediaVersionLocatorRow.media_item_id == review.media_item_id,
                MediaVersionLocatorRow.media_source_id == review.media_source_id,
            )
        )
    )
    versions.update(
        db.scalars(
            select(ReviewMediaVersionRow.version_id).where(
                ReviewMediaVersionRow.review_id == review.id
            )
        )
    )
    return versions


def _require_capturable(db: Session, review: SavedReviewRow, link, versions: set[str]) -> None:
    """Only the review owner may snapshot their own aligned link to the review's version."""
    actor = db.info.get("review_actor")
    if (
        review.owner_id is None
        or not link_usable(link, review.owner_id)
        or (actor is not None and actor.user_id != review.owner_id)
        or link.media_version_id not in versions
    ):
        raise _membership_error(
            "Community filters must be the review owner's master set for this version"
        )
    if link.alignment_state == "required":
        raise _membership_error("Align these community filters before including them in a review")


def kept_plans(review: SavedReviewRow) -> list[SourcePlan]:
    """The stored snapshots, unchanged."""
    return [SourcePlan(row, "keep") for row in review.community_sources]


def prepare_sources(
    db: Session, review: SavedReviewRow, requested, drafts=()
) -> list[SourcePlan] | None:
    """Validate a requested snapshot list; None when it keeps the stored snapshots.

    `requested` holds `ReviewCommunityMembership` values. New links and refreshes take
    their snapshots now (a refresh remembers the entries it replaces); kept snapshots
    are unchanged.
    """
    edited = {draft.link_id: draft for draft in drafts}
    if len(edited) != len(drafts) or set(edited) - {m.link_id for m in requested or ()}:
        raise _membership_error("Community draft filters must reference an included new source")
    if requested is None:
        return None
    existing = {row.id: row for row in review.community_sources}
    plan = [(value.source_id or value.link_id, value) for value in requested]
    unchanged = [key for key, value in plan if value.source_id and not value.refresh]
    if unchanged == list(existing) and len(plan) == len(existing):
        return None
    if len(requested) > MAX_SOURCES:
        raise _membership_error("A review can include at most 32 community filter sets")
    versions = review_version_ids(db, review)
    plans: list[SourcePlan] = []
    links = set()
    for position, (_key, value) in enumerate(plan):
        if value.source_id is not None:
            row = existing.get(value.source_id)
            if row is None or any(item.row is row for item in plans):
                raise _membership_error("Unknown community filter snapshot")
            item = SourcePlan(row, "keep")
            if value.refresh:
                _require_capturable(db, review, row.link, versions)
                item = SourcePlan(row, "refresh", entries_of(row))
                capture(db, row.link, row)
        else:
            link = db.get(CommunityMasterLinkRow, value.link_id)
            _require_capturable(db, review, link, versions)
            row = capture(db, link)
            draft = edited.get(link.id)
            defaults = validate_draft(db, link, draft, entries_of(row)) if draft else None
            item = SourcePlan(row, "new", draft=draft, draft_defaults=defaults)
        if item.row.link_id is not None:
            if item.row.link_id in links:
                raise _membership_error("A review cannot include the same community filters twice")
            links.add(item.row.link_id)
        item.row.position = position
        plans.append(item)
    return plans


def copy_sources(source: SavedReviewRow, duplicate: SavedReviewRow) -> dict[str, str]:
    """Copy every snapshot into a duplicate and return the new ID of each. The link
    reference stays only when the duplicate's owner owns the link, since master links
    are personal."""
    ids = {row.id: str(uuid.uuid4()) for row in source.community_sources}
    duplicate.community_sources = [
        ReviewCommunitySourceRow(
            id=ids[row.id],
            link_id=row.link_id if link_usable(row.link, duplicate.owner_id) else None,
            position=row.position,
            community_version_id=row.community_version_id,
            label=row.label,
            tier=row.tier,
            match_percent=row.match_percent,
            master_revision=row.master_revision,
            alignment_json=row.alignment_json,
            filters_json=row.filters_json,
            filters_digest=row.filters_digest,
            captured_at=row.captured_at,
        )
        for row in source.community_sources
    ]
    return ids


def _sources_of(db: Session, link_ids: Iterable[str]) -> list[ReviewCommunitySourceRow]:
    link_ids = list(link_ids)
    if not link_ids:
        return []
    return list(
        db.scalars(
            select(ReviewCommunitySourceRow).where(ReviewCommunitySourceRow.link_id.in_(link_ids))
        )
    )


def detach_link_sources(db: Session, link_ids: Iterable[str]) -> int:
    """Keep snapshots whose links are being deleted, without the link reference."""
    rows = _sources_of(db, link_ids)
    for row in rows:
        row.link_id = None
        row.link = None
    db.flush()
    return len(rows)


def move_link_sources(db: Session, old_link_id: str, new_link_id: str) -> None:
    """Point snapshots of a link being replaced at the link that replaces it.

    The snapshots and their review filters are unchanged; they report `changed` until
    refreshed. A review that already has a snapshot of the new link keeps the old one
    detached.
    """
    taken = {row.review_id for row in _sources_of(db, [new_link_id])}
    new_link = db.get(CommunityMasterLinkRow, new_link_id)
    for row in _sources_of(db, [old_link_id]):
        if row.review_id in taken:
            row.link_id, row.link = None, None
        else:
            row.link_id, row.link = new_link_id, new_link
    db.flush()


def community_duration(db: Session, review: SavedReviewRow) -> float | None:
    """The shortest known runtime of the review's local versions, in seconds, for
    clipping a community-only review's exports; None when no completed scan records one,
    and the export then probes the media file itself."""
    from .community_master_sources import version_duration_ms

    known = [
        value
        for version in review_version_ids(db, review)
        if (value := version_duration_ms(db, version)) is not None
    ]
    return min(known) / 1000 if known else None


def export_runtime(db: Session, review: SavedReviewRow, scans, probe=None) -> float | None:
    """The runtime exports clip community filters to, in seconds.

    The shortest runtime among the review's scans; for a review without scans, the
    shortest completed scan of its local versions, else `probe()` (the export probes
    its media file; readers that must not touch media pass None).
    """
    durations = [scan.duration_seconds for scan in scans if scan.duration_seconds is not None]
    if durations:
        return min(durations)
    if scans:
        return None
    return community_duration(db, review) or (probe() if probe else None)
