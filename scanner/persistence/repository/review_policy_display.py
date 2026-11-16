"""Expose server-decided review locks without browser policy evaluation."""

from sqlalchemy.orm import Session

from scanner.db import ScanFindingRow

from .review_policy import PreparedReviewPolicy


def locked_severities(db: Session, prepared: PreparedReviewPolicy) -> dict:
    """Project severity locks from immutable source and saved classifications."""
    policy = prepared.policy
    locked = {}
    for value in prepared.filters:
        finding = (
            db.get(ScanFindingRow, value.source_finding_id) if value.source_finding_id else None
        )
        source_category = (
            finding.subcategory
            if finding is not None
            and (finding.scan_id, finding.source) == (value.source_scan_id, value.source)
            else None
        )
        if source_category in policy.severities or value.subcategory in policy.severities:
            locked[value.id] = value.severity
    return locked
