"""Explicit review recovery from the immutable migration repair records."""

import base64
import json
from datetime import datetime, timezone

from sqlalchemy import select

from scanner.db import ReviewFilterRow, ReviewScanMembershipRow, ReviewScanRepairRow, SavedReviewRow
from scanner.models import ReviewRecoveryCounts, ReviewRepairSummary

from ..write_lock import write_lock
from .repository_rows import _utc


def preserved_rows(record):
    """Decode SQLite values, including the migration's explicit BLOB encoding."""
    return json.loads(
        record.preserved_json,
        object_hook=lambda value: (
            base64.b64decode(value["$bytes"]) if set(value) == {"$bytes"} else value
        ),
    )


def repair_records(db, review_id):
    return list(
        db.scalars(
            select(ReviewScanRepairRow)
            .where(ReviewScanRepairRow.review_id == review_id)
            .order_by(ReviewScanRepairRow.missing_scan_id)
        )
    )


def repair_records_by_review(db, review_ids):
    """Batch `repair_records` for many reviews in one query."""
    records = {review_id: [] for review_id in review_ids}
    if records:
        for record in db.scalars(
            select(ReviewScanRepairRow)
            .where(ReviewScanRepairRow.review_id.in_(list(records)))
            .order_by(ReviewScanRepairRow.missing_scan_id)
        ):
            records[record.review_id].append(record)
    return records


def repair_summary(db, review_id):
    return summarize_repairs(repair_records(db, review_id))


def summarize_repairs(records):
    """Summarize one review's repair records; ``None`` when it needs no repair."""
    if not records:
        return None
    preserved = [preserved_rows(record) for record in records]
    return ReviewRepairSummary(
        missing_scan_ids=[record.missing_scan_id for record in records],
        detected_at=_utc(min(record.detected_at for record in records)),
        recover=ReviewRecoveryCounts(
            kept_cues=sum(len(value.get("review_filters", [])) for value in preserved),
            removed_memberships=sum(
                len(value.get("review_scan_memberships", [])) for value in preserved
            ),
            removed_accepted_credits=sum(
                len(value.get("review_accepted_credits", [])) for value in preserved
            ),
        ),
    )


class ReviewNeedsRepair(RuntimeError):
    code = "review_needs_repair"

    def __init__(self, repair):
        super().__init__(repair.message)
        self.repair = repair


def require_repaired(db, review_id):
    repair = repair_summary(db, review_id)
    if repair is not None:
        raise ReviewNeedsRepair(repair)


def recover_review(db, review_id):
    """Convert lost copied cues to manual cues and clear every flag atomically."""
    from .repository_review_core import _review_detail
    from .repository_review_create import manual_review_scan
    from .review_policy_evidence import bind_review_evidence

    with write_lock(db):
        try:
            review = db.get(SavedReviewRow, review_id)
            if review is None:
                return None
            records = repair_records(db, review_id)
            if not records:
                return _review_detail(db, review)
            cues = [
                cue
                for record in records
                for cue in preserved_rows(record).get("review_filters", [])
            ]
            if cues:
                scan = manual_review_scan(
                    db,
                    review.media_item_id,
                    review.media_source_id,
                    {cue["source"] for cue in cues},
                )
                sources = {cue["source"] for cue in cues}
                role = "both" if len(sources) > 1 else ("audio" if "audio" in sources else "video")
                membership = next(
                    (value for value in review.memberships if value.scan_id == scan.id), None
                )
                if membership is None:
                    review.memberships.append(
                        ReviewScanMembershipRow(
                            scan=scan,
                            role=role,
                            position=max(
                                (value.position for value in review.memberships), default=-1
                            )
                            + 1,
                        )
                    )
                elif membership.role != role:
                    membership.role = "both"
                for cue in cues:
                    cue = dict(cue, source_scan_id=scan.id, source_finding_id=None)
                    review.filters.append(ReviewFilterRow(**cue))
                review.filters.sort(key=lambda value: (value.position, value.id))
                for position, cue in enumerate(review.filters):
                    cue.position = position
            bind_review_evidence(db, review)
            review.revision += 1
            review.updated_at = datetime.now(timezone.utc)
            review.content_revision += 1
            review.edl_content_revision += 1
            review.mcf_content_revision += 1
            for record in records:
                db.delete(record)
            db.flush()
            result = _review_detail(db, review)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
