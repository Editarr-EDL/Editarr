"""Persist MCF source evidence and synchronize an isolated editable review."""

import math
import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import (
    MediaItemRow,
    ReviewAcceptedCreditsRow,
    ReviewScanMembershipRow,
    ScanFindingRow,
    ScanMcfDocumentRow,
    ScanRow,
)
from scanner.mcf import (
    McfParseResult,
    TimingInterval,
    category_label,
    map_affine_endpoint,
    map_affine_range,
)
from scanner.models import (
    AcceptedCreditsReference,
    EditAction,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    McfImportResult,
    ReviewFilterInput,
    ReviewMcfDocument,
    ReviewSynchronization,
)
from scanner.repository import (
    CreditsAcceptanceError,
    McfImportTimingError,
    MediaRuntimeUnavailable,
)
from scanner.settings.filter_defaults import enforce_filter_policy

from .filter_preferences import base_filter_settings
from .import_metadata import _mcf_metadata_notices, prepare_mcf_review_documents
from .import_transaction import (
    refresh_imported_rows,
    rollback_on_import_error,
    stage_imported_review,
    stage_imported_scan,
)
from .repository_rows import _scan_from_row
from .review_policy import (
    _map_policy_floor,
    owner_policy,
    policy_write,
    prepare_review_policy,
)
from .review_policy_evidence import bind_review_evidence


@policy_write
def import_mcf_as_saved_review(
    db: Session,
    *,
    media_item: MediaItemRow,
    resolved_local_path: str,
    media_source_id: str | None,
    source_name: str,
    review_name: str,
    parsed: McfParseResult,
    catalog_mcf_document: ReviewMcfDocument,
    duration_seconds: float | None,
    synchronization: ReviewSynchronization,
    mcf_material_end_seconds: float | None,
    accepted_credits: AcceptedCreditsReference | None,
    imported_at: datetime | None = None,
) -> McfImportResult:
    """Create one immutable MCF scan and isolated saved review atomically."""
    with rollback_on_import_error(db):
        return _import_mcf_as_saved_review(
            db,
            media_item=media_item,
            resolved_local_path=resolved_local_path,
            media_source_id=media_source_id,
            source_name=source_name,
            review_name=review_name,
            parsed=parsed,
            catalog_mcf_document=catalog_mcf_document,
            duration_seconds=duration_seconds,
            synchronization=synchronization,
            mcf_material_end_seconds=mcf_material_end_seconds,
            accepted_credits=accepted_credits,
            imported_at=imported_at,
        )


def _import_mcf_as_saved_review(
    db: Session,
    *,
    media_item: MediaItemRow,
    resolved_local_path: str,
    media_source_id: str | None,
    source_name: str,
    review_name: str,
    parsed: McfParseResult,
    catalog_mcf_document: ReviewMcfDocument,
    duration_seconds: float | None,
    synchronization: ReviewSynchronization,
    mcf_material_end_seconds: float | None,
    accepted_credits: AcceptedCreditsReference | None,
    imported_at: datetime | None,
) -> McfImportResult:
    """Validate MCF cues, metadata, and timing before persisting an imported review."""
    if duration_seconds is None or not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise MediaRuntimeUnavailable("Media runtime is unavailable")
    source_interval = TimingInterval(
        start_seconds=parsed.source_metadata.material_start_seconds,
        end_seconds=parsed.source_metadata.material_end_seconds,
    )
    target_interval = TimingInterval(
        start_seconds=synchronization.start_seconds,
        end_seconds=synchronization.end_seconds,
    )
    try:
        map_affine_range(
            source_interval.start_seconds,
            source_interval.end_seconds,
            source_interval,
            target_interval,
            duration_seconds=duration_seconds,
        )
    except ValueError as exc:
        raise McfImportTimingError(
            "invalid_synchronization_bounds",
            str(exc),
            document_fields=["synchronization"],
        ) from exc
    if synchronization.end_mode.value == "before_credits":
        if mcf_material_end_seconds != synchronization.end_seconds:
            raise ValueError("Before-credits material end must match synchronization end")
    elif mcf_material_end_seconds is not None and not (
        synchronization.start_seconds < mcf_material_end_seconds <= synchronization.end_seconds
    ):
        raise ValueError("File-end material end must be within the synchronization interval")
    accepted_detection = None
    if accepted_credits is not None:
        accepted_scan, candidate, accepted_finding = root._validated_credits_acceptance(
            db,
            scan_id=accepted_credits.scan_id,
            accepted_boundary_seconds=accepted_credits.accepted_boundary_seconds,
            media_item_id=media_item.id,
            media_source_id=media_source_id or "",
            resolved_local_path=resolved_local_path,
        )
        if candidate.id != accepted_credits.candidate_id:
            raise CreditsAcceptanceError(
                "credits_candidate_not_found",
                "Credits detection candidate was not found",
                404,
            )
        accepted_detection = (accepted_scan, candidate, accepted_finding)
    imported_at = imported_at or datetime.now(timezone.utc)
    source_id = media_source_id or None
    channels = {entry.mcf_channel for entry in parsed.entries}
    has_audio = not channels or any(
        channel in {McfChannel.AUDIO, McfChannel.BOTH} for channel in channels
    )
    has_video = not channels or any(
        channel in {McfChannel.VIDEO, McfChannel.BOTH} for channel in channels
    )
    role = "both" if has_audio and has_video else ("audio" if has_audio else "video")
    next_number = (
        db.query(func.max(ScanRow.scan_number))
        .filter(ScanRow.media_item_id == media_item.id)
        .scalar()
        or 0
    ) + 1
    scan = stage_imported_scan(
        db,
        media_item=media_item,
        scan_number=next_number,
        review_name=review_name,
        source_name=source_name,
        imported_at=imported_at,
        resolved_local_path=resolved_local_path,
        media_source_id=source_id,
        duration_seconds=duration_seconds,
        has_audio=has_audio,
        has_video=has_video,
        progress_message="Imported MCF",
        mcf_document=ScanMcfDocumentRow(**parsed.source_metadata.model_dump()),
    )

    findings: list[ScanFindingRow] = []
    for entry in parsed.entries:
        source = "audio" if entry.mcf_channel == McfChannel.AUDIO else "visual"
        finding = ScanFindingRow(
            scan_id=scan.id,
            category=entry.category,
            subcategory=entry.subcategory,
            severity=entry.severity,
            mcf_channel=entry.mcf_channel,
            mcf_comment=entry.mcf_comment,
            start_seconds=entry.start_seconds,
            end_seconds=entry.end_seconds,
            confidence=None,
            label=category_label(entry.subcategory),
            details=entry.mcf_comment,
            source=source,
            selected=True,
            edit_action=(EditAction.MUTE.value if source == "audio" else EditAction.SKIP.value),
        )
        db.add(finding)
        findings.append(finding)
    db.flush()

    review = stage_imported_review(
        db,
        media_item=media_item,
        media_source_id=source_id,
        review_name=review_name,
        imported_at=imported_at,
    )
    review.synchronization = root._review_synchronization_row(
        review.id,
        synchronization,
    )
    membership = ReviewScanMembershipRow(
        review_id=review.id,
        scan_id=scan.id,
        role=role,
        position=0,
        scan=scan,
    )
    memberships = {scan.id: membership}
    if accepted_detection is not None:
        accepted_scan, _candidate, _accepted_finding = accepted_detection
        memberships[accepted_scan.id] = ReviewScanMembershipRow(
            review_id=review.id,
            scan_id=accepted_scan.id,
            role="video",
            position=1,
            scan=accepted_scan,
        )
    review.memberships = list(memberships.values())
    imported_document, review_document = prepare_mcf_review_documents(
        parsed.source_metadata, catalog_mcf_document, synchronization, mcf_material_end_seconds
    )
    root._validate_review_mcf_runtime(review_document, {scan.id: membership})
    review.mcf_document = root._review_mcf_document_row(review.id, review_document)
    review_inputs = [
        ReviewFilterInput(
            id=str(uuid.uuid4()),
            source_scan_id=scan.id,
            source_finding_id=finding.id,
            source=finding.source,
            category=finding.category,
            subcategory=finding.subcategory,
            severity=finding.severity,
            mcf_channel=finding.mcf_channel,
            mcf_comment=finding.mcf_comment,
            label=finding.label,
            details=finding.details,
            group_key=None,
            start_seconds=finding.start_seconds,
            end_seconds=finding.end_seconds,
            confidence=finding.confidence,
            edit_action=finding.edit_action,
            selected=finding.selected,
        )
        for finding in findings
    ]
    review_inputs = root.fill_filter_defaults(
        review_inputs, base_filter_settings(db, review.owner_id)
    ).filters
    policy = owner_policy(db, review.owner_id)
    try:
        mapped_inputs = []
        for entry in review_inputs:
            required = (
                enforce_filter_policy([entry.model_copy(update={"selected": False})], policy)
                .filters[0]
                .selected
            )
            if required:
                for endpoint in (entry.start_seconds, entry.end_seconds):
                    if endpoint is not None:
                        map_affine_endpoint(
                            endpoint,
                            source_interval,
                            target_interval,
                            duration_seconds=duration_seconds,
                        )
                start, end = _map_policy_floor(
                    entry.start_seconds,
                    entry.end_seconds,
                    source_interval,
                    target_interval,
                    duration_seconds=duration_seconds,
                )
            else:
                start, end = map_affine_range(
                    entry.start_seconds,
                    entry.end_seconds,
                    source_interval,
                    target_interval,
                    duration_seconds=duration_seconds,
                )
            mapped_inputs.append(
                entry.model_copy(update={"start_seconds": start, "end_seconds": end})
            )
        review_inputs = mapped_inputs
    except ValueError as exc:
        raise McfImportTimingError("mapped_cue_out_of_bounds", str(exc)) from exc
    if accepted_detection is not None:
        accepted_scan, candidate, accepted_finding = accepted_detection
        review_inputs.append(
            ReviewFilterInput(
                id=str(uuid.uuid4()),
                source_scan_id=accepted_scan.id,
                source_finding_id=accepted_finding.id,
                source="visual",
                category=McfCategoryFamily.DISPENSABLE_SCENES,
                subcategory=McfCategory.dispensable,
                severity=accepted_finding.severity,
                mcf_channel=McfChannel.VIDEO,
                mcf_comment="End Credits",
                label="End Credits",
                details="End Credits",
                group_key=accepted_finding.group_key,
                start_seconds=accepted_credits.accepted_boundary_seconds,
                end_seconds=accepted_scan.duration_seconds,
                confidence=candidate.confidence,
                edit_action=EditAction.SKIP,
                selected=True,
            )
        )
    review.filters = root._filter_rows_from_inputs(
        db,
        review_id=review.id,
        memberships=memberships,
        filters=review_inputs,
    )
    if accepted_detection is not None:
        accepted_scan, candidate, accepted_finding = accepted_detection
        review.accepted_credits = ReviewAcceptedCreditsRow(
            review_id=review.id,
            scan_id=accepted_scan.id,
            source_finding_id=accepted_finding.id,
            candidate_id=candidate.id,
            detected_boundary_seconds=candidate.boundary_seconds,
            accepted_boundary_seconds=accepted_credits.accepted_boundary_seconds,
            confidence=candidate.confidence,
            method=candidate.method,
            accepted_at=imported_at,
        )
    notices = _mcf_metadata_notices(imported_document, catalog_mcf_document)
    bind_review_evidence(db, review)
    prepare_review_policy(db, review)
    db.commit()
    refresh_imported_rows(db, scan, review)
    return McfImportResult(
        review=root._review_detail(db, review),
        scan=_scan_from_row(scan, finding_count=len(findings)),
        source_version=parsed.source_version,
        notices=notices,
    )
