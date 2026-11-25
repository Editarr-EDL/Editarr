"""Apply a saved review's community language mutes to subtitles through local findings.

Community filters carry no words, so they redact subtitles only through a word-level
language finding of a completed local audio scan of the review's version that they
align with: the same top-level category, overlapping on the local timeline (the
rule that marks a community filter "Locally verified"). Such a mute then redacts
that finding's words exactly like a scan mute. Without an aligned finding the
subtitles are left untouched, and `community_subtitle_summary` reports why.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from scanner.db import SavedReviewRow, ScanFindingRow, ScanMediaVersionRow, ScanRow
from scanner.formats.subtitle_redaction import SubtitleMute
from scanner.modeling.community_master_state import intervals_overlap
from scanner.models import CommunitySubtitleSummary, McfCategoryFamily

from .review_community_export import exportable
from .review_community_sources import review_filters, review_version_ids
from .version_reuse import source_pending_reuse


@dataclass(frozen=True)
class CommunitySubtitleMutes:
    mutes: tuple[SubtitleMute, ...]
    summary: CommunitySubtitleSummary


def _accepted_scan_ids(db: Session, review: SavedReviewRow) -> list[str]:
    """Completed local audio scans of the review's version, as export and "Locally
    verified" accept them: bound to one of the review's local versions, or unbound and
    of the review's item and source. Scans awaiting version-reuse alignment are skipped."""
    versions = review_version_ids(db, review)
    source = review.media_source_id or ""
    candidates = db.execute(
        select(ScanRow, ScanMediaVersionRow.version_id)
        .outerjoin(ScanMediaVersionRow, ScanMediaVersionRow.scan_id == ScanRow.id)
        .where(
            ScanRow.status == "complete",
            ScanRow.scan_audio.is_(True),
            ScanRow.origin == "generated",
            or_(
                ScanMediaVersionRow.version_id.in_(versions),
                (ScanRow.media_item_id == review.media_item_id)
                & (func.coalesce(ScanRow.media_source_id, "") == source),
            ),
        )
    ).all()
    return [
        scan.id
        for scan, version in candidates
        if not source_pending_reuse(scan)
        and (
            version in versions
            if version is not None
            else scan.media_item_id == review.media_item_id
        )
    ]


def _local_words(
    db: Session, review: SavedReviewRow
) -> list[tuple[float, float | None, str]] | None:
    """Word-level language findings of the version's completed local audio scans, or None
    when the version has no such scan."""
    scans = _accepted_scan_ids(db, review)
    if not scans:
        return None
    rows = db.execute(
        select(
            ScanFindingRow.start_seconds, ScanFindingRow.end_seconds, ScanFindingRow.label
        ).where(
            ScanFindingRow.scan_id.in_(scans),
            ScanFindingRow.source == "audio",
            ScanFindingRow.category == McfCategoryFamily.LANGUAGE,
            ScanFindingRow.label.is_not(None),
            ScanFindingRow.end_seconds > ScanFindingRow.start_seconds,
        )
    ).all()
    return [tuple(row) for row in rows]


def community_subtitle_mutes(
    db: Session, review: SavedReviewRow, duration_seconds: float | None
) -> CommunitySubtitleMutes:
    """Subtitle mutes for the review's community language mutes, and what was applied."""
    filters = [
        item
        for item in exportable(review_filters(review), duration_seconds=duration_seconds)
        if item.category == "language" and item.action == "mute"
    ]
    if not filters:
        return CommunitySubtitleMutes((), CommunitySubtitleSummary())
    words = _local_words(db, review)
    if words is None:
        summary = CommunitySubtitleSummary(needs_audio_scan=len(filters))
        return CommunitySubtitleMutes((), summary)
    mutes: dict[tuple[float, float | None, str], SubtitleMute] = {}
    applied = unaligned = 0
    for item in filters:
        aligned = [
            word
            for word in words
            if intervals_overlap(item.start_ms, item.end_ms, word[0], word[1])
        ]
        if not aligned:
            unaligned += 1
            continue
        applied += 1
        for start, end, label in aligned:
            mutes[(start, end, label)] = SubtitleMute(start, end, label)
    summary = CommunitySubtitleSummary(applied=applied, unaligned=unaligned)
    return CommunitySubtitleMutes(tuple(mutes.values()), summary)
