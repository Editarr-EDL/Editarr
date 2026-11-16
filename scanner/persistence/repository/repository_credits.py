"""Persistence and readiness checks for credits detection scans."""

import json
import math
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from scanner.db import ScanFindingRow, ScanRow
from scanner.models import (
    CreditsDetectionConfiguration,
    CreditsDetectionResult,
    EditAction,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    Scan,
    ScanMode,
)

from .repository_rows import _scan_from_row
from .repository_settings import (
    get_structured_filter_defaults_settings,
)


class MediaRuntimeUnavailable(ValueError):
    """A review cannot establish its synchronization basis without a trusted runtime."""


def _credits_readiness_failure(scan: ScanRow | None) -> tuple[str, str, int] | None:
    """Describe why a credits scan cannot yet be accepted or previewed."""
    if (
        scan is None
        or scan.scan_mode != ScanMode.CREDITS_DETECTION
        or scan.credits_detection is None
    ):
        return "scan_not_found", "Credits detection scan was not found", 404
    if scan.status == "cancelled":
        return "credits_detection_cancelled", "Credits detection was cancelled", 409
    if scan.status == "failed":
        if scan.error_message == "Optional model acquisition failed":
            return (
                "credits_model_acquisition_failed",
                "Optional model acquisition failed",
                503,
            )
        return "credits_detection_failed", "Credits detection failed", 500
    if scan.status == "awaiting_model_consent":
        return (
            "credits_model_consent_required",
            "Credits detection requires model consent",
            409,
        )
    if (
        scan.status != "complete"
        or scan.credits_detection.result_json is None
        or scan.credits_detection.completed_at is None
    ):
        return (
            "credits_detection_not_ready",
            "Credits detection is not ready",
            409,
        )
    return None


def complete_credits_detection(
    db: Session,
    *,
    scan_id: str,
    cache_key: str,
    result: CreditsDetectionResult,
) -> Scan:
    """Persist detector output and its candidate finding for a scan."""
    row = db.get(ScanRow, scan_id)
    if row is None or row.scan_mode != ScanMode.CREDITS_DETECTION or row.credits_detection is None:
        raise ValueError("Credits detection scan was not found")
    if (
        row.duration_seconds is None
        or not math.isfinite(row.duration_seconds)
        or row.duration_seconds <= 0
    ):
        raise MediaRuntimeUnavailable("Media runtime is unavailable")
    if row.credits_detection.completed_at is not None:
        raise ValueError("Credits detection scan is already complete")

    finding: ScanFindingRow | None = None
    if result.outcome == "candidate":
        candidate = result.candidates[0]
        if (
            candidate.boundary_seconds >= row.duration_seconds
            or candidate.range_end_seconds > row.duration_seconds
        ):
            raise ValueError("Credits candidate exceeds the media runtime")
        severity = next(
            default.severity
            for default in get_structured_filter_defaults_settings(db).severity_defaults
            if default.subcategory == McfCategory.dispensable
        )
        finding = ScanFindingRow(
            scan_id=scan_id,
            category=McfCategoryFamily.DISPENSABLE_SCENES,
            subcategory=McfCategory.dispensable,
            severity=severity,
            mcf_channel=McfChannel.VIDEO,
            mcf_comment="End Credits",
            start_seconds=candidate.boundary_seconds,
            end_seconds=row.duration_seconds,
            confidence=candidate.confidence,
            label="End Credits",
            details="End Credits",
            source="visual",
            selected=True,
            edit_action=EditAction.SKIP.value,
            group_key=McfCategoryFamily.DISPENSABLE_SCENES.value,
        )
        db.add(finding)

    now = datetime.now(timezone.utc)
    row.credits_detection.cache_key = cache_key
    row.credits_detection.result_json = json.dumps(
        result.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    row.credits_detection.completed_at = now
    row.status = "complete"
    row.progress_percent = 100.0
    row.progress_stage = "complete"
    row.progress_message = "Credits detection complete"
    row.updated_at = now
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    return _scan_from_row(row, finding_count=1 if finding is not None else 0)


def update_credits_detection_configuration(
    db: Session,
    scan_id: str,
    configuration: CreditsDetectionConfiguration,
    *,
    status: str,
    progress_message: str,
) -> Scan:
    """Persist consented model settings and queue status for a credits scan."""
    row = db.get(ScanRow, scan_id)
    if row is None or row.scan_mode != ScanMode.CREDITS_DETECTION or row.credits_detection is None:
        raise ValueError("Credits detection scan was not found")
    row.credits_detection.configuration_json = json.dumps(
        configuration.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    row.credits_detection.detector_version = configuration.detector_version
    row.status = status
    row.progress_stage = status
    row.progress_message = progress_message
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _scan_from_row(row)
