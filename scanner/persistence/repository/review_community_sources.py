"""Community filter snapshots in saved reviews (local API §3, review membership).

Including a master link in a review stores a snapshot of the link's active filters
(after the owner's decisions, threshold, Low Confidence preference, approved alignment
and accepted updates). Review results, exports and sidecars read only that snapshot.
Later changes to the link never touch the review; the review reports them as
`changed`, and the owner refreshes the snapshot by saving the review, which advances
its revisions like any other content change.
"""

from __future__ import annotations

import hashlib
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
from scanner.models import ReviewCommunityFilter, ReviewCommunitySource

from .community_master_links import alignment_of, utcnow
from .community_master_sources import ActiveMasterFilter, link_active_filters
from .review_community_export import unusable_filter_ids

MAX_SOURCES = 32


def _membership_error(message: str) -> ValueError:
    from scanner.repository import ReviewMembershipError

    return ReviewMembershipError(message)


# The label of an unlabeled candidate version, as in the master view.
UNLABELED = "Other"


def _filter_json(item: ActiveMasterFilter) -> dict:
    return {
        "filter_id": item.filter_id,
        "category": item.category,
        "subcategory": item.subcategory,
        "severity": item.severity,
        "edit_action": item.action,
        "start_ms": item.start_ms,
        "end_ms": item.end_ms,
        "confidence": item.confidence,
    }


def filters_digest(filters: Iterable[dict]) -> str:
    """Identify snapshot filter values; confidence is informational and left out."""
    document = [
        {key: value for key, value in item.items() if key != "confidence"} for item in filters
    ]
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def link_usable(link: CommunityMasterLinkRow | None, owner_id: int | None) -> bool:
    """Whether a snapshot can be (re)taken from `link` for a review that `owner_id` owns."""
    return link is not None and link.owner_id == owner_id and link.link_state != "replaced"


def live_filters(db: Session, link: CommunityMasterLinkRow) -> list[dict]:
    """The link's active filters now, unclipped, in snapshot form; exports clip them."""
    return [_filter_json(item) for item in link_active_filters(db, link.owner_id, link, clip=False)]


def capture(
    db: Session, link: CommunityMasterLinkRow, row: ReviewCommunitySourceRow | None = None
) -> ReviewCommunitySourceRow:
    """Take a snapshot of the link's active filters into `row`, or a new row."""
    filters = live_filters(db, link)
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
    row.filters_json = json.dumps(filters, sort_keys=True, separators=(",", ":"))
    row.filters_digest = filters_digest(filters)
    row.captured_at = utcnow()
    return row


def source_state(db: Session, review: SavedReviewRow, row: ReviewCommunitySourceRow) -> str:
    """How a stored snapshot compares with its link's active filters now."""
    link = row.link
    if not link_usable(link, review.owner_id):
        return "link_unavailable"
    if link.alignment_state == "required":
        return "alignment_required"
    current = filters_digest(live_filters(db, link)) == row.filters_digest
    return "current" if current else "changed"


def _snapshot_filters(row) -> list[dict]:
    return json.loads(row.filters_json)


def review_sources(db: Session, review: SavedReviewRow) -> list[ReviewCommunitySource]:
    """Each stored snapshot with its filters and its state against the link now."""
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
            filters=[
                ReviewCommunityFilter(
                    **{key: value for key, value in item.items() if not key.endswith("_ms")},
                    start_seconds=item["start_ms"] / 1000,
                    end_seconds=item["end_ms"] / 1000,
                )
                for item in _snapshot_filters(row)
            ],
            unusable_filter_ids=unusable_filter_ids(
                _as_active(item, row.id) for item in _snapshot_filters(row)
            ),
        )
        for row in review.community_sources
    ]


def _as_active(item: dict, link_id: str) -> ActiveMasterFilter:
    return ActiveMasterFilter(
        link_id,
        item["filter_id"],
        item["category"],
        item["start_ms"],
        item["end_ms"],
        item["subcategory"],
        item["severity"],
        item["edit_action"],
        item.get("confidence"),
    )


def review_filters(review: SavedReviewRow) -> list[ActiveMasterFilter]:
    """Every stored snapshot filter of the review, in membership order."""
    return [
        _as_active(item, row.link_id or row.id)
        for row in review.community_sources
        for item in _snapshot_filters(row)
    ]


def snapshot_filters(review_data: dict) -> list[ActiveMasterFilter]:
    """The snapshot filters recorded in a retained review export snapshot."""
    return [
        ActiveMasterFilter(
            source.get("link_id") or source["source_id"],
            value["filter_id"],
            value["category"],
            round(value["start_seconds"] * 1000),
            round(value["end_seconds"] * 1000),
            value["subcategory"],
            value["severity"],
            value["edit_action"],
            value.get("confidence"),
        )
        for source in review_data.get("community_sources", ())
        for value in source.get("filters", ())
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


def prepare_sources(db: Session, review: SavedReviewRow, requested) -> list | None:
    """Validate a requested snapshot list; None when it keeps the stored snapshots.

    `requested` holds `ReviewCommunityMembership` values. New links and refreshes take
    their snapshots now; kept snapshots are unchanged.
    """
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
    rows, links = [], set()
    for position, (_key, value) in enumerate(plan):
        if value.source_id is not None:
            row = existing.get(value.source_id)
            if row is None or row in rows:
                raise _membership_error("Unknown community filter snapshot")
            if value.refresh:
                _require_capturable(db, review, row.link, versions)
                capture(db, row.link, row)
        else:
            link = db.get(CommunityMasterLinkRow, value.link_id)
            _require_capturable(db, review, link, versions)
            row = capture(db, link)
        if row.link_id is not None:
            if row.link_id in links:
                raise _membership_error("A review cannot include the same community filters twice")
            links.add(row.link_id)
        row.position = position
        rows.append(row)
    return rows


def copy_sources(source: SavedReviewRow, duplicate: SavedReviewRow) -> None:
    """Copy every snapshot into a duplicate. The link reference stays only when the
    duplicate's owner owns the link, since master links are personal."""
    duplicate.community_sources = [
        ReviewCommunitySourceRow(
            id=str(uuid.uuid4()),
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

    The snapshots themselves are unchanged; they report `changed` until refreshed. A
    review that already has a snapshot of the new link keeps the old one detached.
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
