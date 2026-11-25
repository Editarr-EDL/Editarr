"""Read-only community provenance of a saved review's community filters (local API §3)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import SavedReviewRow, ScanFindingRow, ScanMediaVersionRow, ScanRow
from scanner.modeling.community_review_filters import served
from scanner.models import ReviewFilterCommunity

from .review_community_filters import entries_of


def annotations(db: Session, review: SavedReviewRow) -> dict[str, ReviewFilterCommunity]:
    """Read-only community provenance for each community filter of the review, by ID."""
    sources = {
        row.id: (row, {e["filter_id"]: e for e in entries_of(row)})
        for row in review.community_sources
    }
    findings = _local_findings(db, review) if sources else {}
    found = {}
    for row in review.filters:
        if row.community_source_id is None or row.community_source_id not in sources:
            continue
        source, entries = sources[row.community_source_id]
        entry = entries.get(row.community_filter_id) or {}
        category = getattr(row.category, "value", row.category) or entry.get("category")
        end = row.end_seconds if row.end_seconds is not None else row.start_seconds
        found[row.id] = ReviewFilterCommunity(
            source_id=source.id,
            filter_id=row.community_filter_id,
            label=source.label,
            master_revision=source.master_revision,
            confidence=entry.get("confidence"),
            contributor_count=entry.get("contributor_count"),
            low_confidence=bool(entry.get("low_confidence")),
            removed=bool(entry) and not served(entry),
            locally_verified=any(
                start <= end and row.start_seconds <= (stop if stop is not None else start)
                for start, stop in findings.get(category, ())
            ),
        )
    return found


def _local_findings(db: Session, review: SavedReviewRow) -> dict[str, list]:
    """Finding intervals by top-level category from completed local model scans of the
    review's versions or among its own scans, for Locally verified (U7)."""
    from .review_community_sources import review_version_ids

    versions = review_version_ids(db, review)
    members = [value.scan_id for value in review.memberships]
    scans = select(ScanMediaVersionRow.scan_id).where(ScanMediaVersionRow.version_id.in_(versions))
    rows = db.execute(
        select(ScanFindingRow.category, ScanFindingRow.start_seconds, ScanFindingRow.end_seconds)
        .join(ScanRow, ScanRow.id == ScanFindingRow.scan_id)
        .where(
            ScanRow.origin == "generated",
            ScanRow.status == "complete",
            ScanFindingRow.category.is_not(None),
            ScanRow.id.in_(scans) | ScanRow.id.in_(members),
        )
    ).all()
    found: dict[str, list] = {}
    for category, start, end in rows:
        found.setdefault(getattr(category, "value", category), []).append((start, end))
    return found
