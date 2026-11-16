"""Saved review MCF metadata, timing, and accepted credits helpers."""

import math
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import (
    MediaItemRow,
    ReviewAcceptedCreditsRow,
    ReviewMcfDocumentRow,
    ReviewScanMembershipRow,
    ReviewSynchronizationRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.models import (
    AcceptedCreditsReference,
    EditAction,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    McfMetadataSeedCandidate,
    McfWorkType,
    ReviewFilterInput,
    ReviewMcfDocument,
    ReviewScanMembership,
    ReviewSynchronization,
    ScanOrigin,
)
from scanner.repository import (
    McfMetadataSourceRequired,
    MediaRuntimeUnavailable,
    ReviewMembershipError,
)


def _review_mcf_document_row(review_id: str, document: ReviewMcfDocument) -> ReviewMcfDocumentRow:
    return ReviewMcfDocumentRow(review_id=review_id, **document.model_dump())


def _default_review_synchronization(
    duration_seconds: float | None,
) -> ReviewSynchronization:
    """Choose the initial synchronization range from the review media."""
    if duration_seconds is None or not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise MediaRuntimeUnavailable("Media runtime is unavailable")
    return ReviewSynchronization(
        start_seconds=0,
        end_seconds=duration_seconds,
        end_mode="file_end",
    )


def _review_synchronization_row(
    review_id: str, synchronization: ReviewSynchronization
) -> ReviewSynchronizationRow:
    return ReviewSynchronizationRow(review_id=review_id, **synchronization.model_dump())


def _validate_synchronization_document(
    synchronization: ReviewSynchronization, document: ReviewMcfDocument
) -> None:
    """Check document timing bounds against the synchronization range."""
    if document.material_start_seconds != synchronization.start_seconds:
        raise ValueError("MCF material start must match synchronization start")
    if synchronization.end_mode.value == "before_credits":
        if document.material_end_seconds != synchronization.end_seconds:
            raise ValueError("Before-credits material end must match synchronization end")
    elif (
        document.material_end_seconds is not None
        and document.material_end_seconds > synchronization.end_seconds
    ):
        raise ValueError("MCF material end cannot exceed file-end synchronization")


def _document_for_synchronization(
    document: ReviewMcfDocument, synchronization: ReviewSynchronization
) -> ReviewMcfDocument:
    """Adjust MCF material bounds to match a review synchronization range."""
    updates: dict[str, float] = {"material_start_seconds": synchronization.start_seconds}
    if synchronization.end_mode.value == "before_credits":
        updates["material_end_seconds"] = synchronization.end_seconds
    synchronized = document.model_copy(update=updates)
    _validate_synchronization_document(synchronization, synchronized)
    return synchronized


def _accepted_credits_signature(value) -> tuple[object, ...] | None:
    if value is None:
        return None
    return (
        value.scan_id,
        value.candidate_id,
        value.accepted_boundary_seconds,
    )


def _accepted_credits_row_for_save(
    db: Session,
    review: SavedReviewRow,
    persisted: ReviewAcceptedCreditsRow | None,
    requested: AcceptedCreditsReference | None,
    memberships: list[ReviewScanMembership],
    filters: list[ReviewFilterInput],
) -> ReviewAcceptedCreditsRow | None:
    """Validate accepted credits and prepare their persisted provenance."""
    if requested is None:
        return None
    scan, candidate, finding = root._validated_credits_acceptance(
        db,
        scan_id=requested.scan_id,
        accepted_boundary_seconds=requested.accepted_boundary_seconds,
        media_item_id=review.media_item_id,
        media_source_id=review.media_source_id,
    )
    if requested.candidate_id != candidate.id:
        raise ReviewMembershipError("Accepted credits candidate is invalid")
    membership = next((value for value in memberships if value.scan_id == scan.id), None)
    if membership is None or membership.role not in {"video", "both"}:
        raise ReviewMembershipError("Accepted credits require a video scan membership")
    _accepted_credits_snapshot(scan, finding, requested, filters)
    preserve_detector_provenance = (
        persisted is not None
        and persisted.scan_id == scan.id
        and persisted.candidate_id == candidate.id
    )
    if preserve_detector_provenance:
        persisted.accepted_boundary_seconds = requested.accepted_boundary_seconds
        return persisted
    return ReviewAcceptedCreditsRow(
        review_id=review.id,
        scan_id=scan.id,
        source_finding_id=finding.id,
        candidate_id=candidate.id,
        detected_boundary_seconds=candidate.boundary_seconds,
        accepted_boundary_seconds=requested.accepted_boundary_seconds,
        confidence=candidate.confidence,
        method=candidate.method,
        accepted_at=datetime.now(timezone.utc),
    )


def _accepted_credits_snapshot(
    scan: ScanRow,
    finding: ScanFindingRow,
    accepted: AcceptedCreditsReference,
    filters: list[ReviewFilterInput],
) -> ReviewFilterInput:
    """Expose accepted credits with their source scan and finding."""
    snapshot = next(
        (
            value
            for value in filters
            if value.source_scan_id == scan.id and value.source_finding_id == finding.id
        ),
        None,
    )
    if (
        snapshot is None
        or snapshot.source != "visual"
        or snapshot.start_seconds != accepted.accepted_boundary_seconds
        or snapshot.end_seconds is None
        or snapshot.category != McfCategoryFamily.DISPENSABLE_SCENES
        or snapshot.subcategory != McfCategory.dispensable
        or snapshot.mcf_channel != McfChannel.VIDEO
        or snapshot.mcf_comment != "End Credits"
        or snapshot.edit_action != EditAction.SKIP
        or not snapshot.selected
    ):
        raise ReviewMembershipError("Accepted credits snapshot is invalid")
    return snapshot


def _seed_review_mcf_document(
    media: MediaItemRow,
    requested_memberships: list[ReviewScanMembership],
    membership_rows: dict[str, ReviewScanMembershipRow],
    requested_seed,
    catalog_document: ReviewMcfDocument | None,
) -> tuple[ReviewMcfDocument, bool]:
    """Select MCF metadata from explicit or inferred scan sources."""
    catalog = catalog_document or _catalog_review_mcf_document(media, membership_rows)
    imported: list[tuple[ScanRow, ReviewMcfDocument]] = []
    for membership in requested_memberships:
        scan = membership_rows[membership.scan_id].scan
        if scan.origin != ScanOrigin.IMPORTED or scan.mcf_document is None:
            continue
        imported.append(
            (
                scan,
                ReviewMcfDocument.model_validate(scan.mcf_document, from_attributes=True),
            )
        )

    selected: ReviewMcfDocument
    selected_from_import = False
    if requested_seed is not None and requested_seed.kind == "scan":
        selected = next(
            (document for scan, document in imported if scan.id == requested_seed.scan_id),
            None,
        )
        if selected is None:
            raise ReviewMembershipError("MCF metadata scan must be an included imported scan")
        selected_from_import = True
    elif requested_seed is not None:
        selected = catalog
    elif imported:
        distinct = {tuple(document.model_dump().items()) for _scan, document in imported}
        if len(distinct) > 1:
            candidates = [
                McfMetadataSeedCandidate(
                    kind="scan",
                    scan_id=scan.id,
                    label=scan.name or f"Scan {scan.scan_number}",
                    mcf_document=document,
                )
                for scan, document in imported
            ]
            candidates.append(
                McfMetadataSeedCandidate(
                    kind="catalog",
                    label="Catalog/runtime defaults",
                    mcf_document=catalog,
                )
            )
            raise McfMetadataSourceRequired(candidates)
        selected = imported[0][1]
        selected_from_import = True
    else:
        selected = catalog
    document = _fill_mcf_document_gaps(selected, catalog)
    _validate_review_mcf_runtime(document, membership_rows)
    return document, selected_from_import


def _catalog_review_mcf_document(
    media: MediaItemRow,
    memberships: dict[str, ReviewScanMembershipRow],
) -> ReviewMcfDocument:
    """Build initial MCF metadata from the catalog target."""
    work_type = {
        "movie": McfWorkType.MOVIE,
        "episode": McfWorkType.EPISODE,
        "series": McfWorkType.SERIES,
    }.get(media.item_type.casefold())
    duration = next(
        (
            membership.scan.duration_seconds
            for membership in memberships.values()
            if membership.scan.duration_seconds is not None
        ),
        None,
    )
    return ReviewMcfDocument(
        title=media.name,
        work_type=work_type,
        material_start_seconds=0,
        material_end_seconds=duration,
    )


def _fill_mcf_document_gaps(
    primary: ReviewMcfDocument, fallback: ReviewMcfDocument
) -> ReviewMcfDocument:
    values = primary.model_dump()
    for field, value in fallback.model_dump().items():
        if values[field] is None:
            values[field] = value
    if values["material_start_seconds"] is None:
        values["material_start_seconds"] = 0
    return ReviewMcfDocument(**values)


def _validate_review_mcf_runtime(
    document: ReviewMcfDocument,
    memberships: dict[str, ReviewScanMembershipRow],
    *,
    review: SavedReviewRow | None = None,
) -> None:
    """Check MCF material bounds against the physical media runtime."""
    from .community_import_targets import review_duration

    known_durations = [
        duration
        for membership in memberships.values()
        if (
            duration := review_duration(
                membership.scan,
                review.media_item_id if review else None,
                review.media_source_id if review else None,
            )
        )
        is not None
    ]
    if (
        document.material_end_seconds is not None
        and known_durations
        and document.material_end_seconds > min(known_durations)
    ):
        raise ValueError("MCF material end exceeds media duration")


def _validate_review_synchronization_runtime(
    review: SavedReviewRow,
    synchronization: ReviewSynchronization,
    memberships: dict[str, ReviewScanMembershipRow],
) -> None:
    """Ensure a review synchronization interval fits the playable media."""
    from .community_import_targets import review_duration

    known_durations = [
        duration
        for membership in memberships.values()
        if (
            duration := review_duration(
                membership.scan, review.media_item_id, review.media_source_id
            )
        )
        is not None
    ]
    runtime = min(known_durations) if known_durations else review.synchronization.end_seconds
    if synchronization.end_seconds > runtime:
        raise ValueError("Synchronization end exceeds media duration")


def _with_manual_membership(
    memberships: list[ReviewScanMembership],
    manual_scan_id: str,
    filters: list[ReviewFilterInput],
) -> list[ReviewScanMembership]:
    """Attach a manual scan and its filters to the review draft."""
    required_sources = {value.source for value in filters}
    required_role = (
        "both"
        if required_sources == {"audio", "visual"}
        else ("audio" if "audio" in required_sources else "video")
    )
    result = list(memberships)
    for index, membership in enumerate(result):
        if membership.scan_id != manual_scan_id:
            continue
        has_audio = membership.role in {"audio", "both"} or required_role in {
            "audio",
            "both",
        }
        has_video = membership.role in {"video", "both"} or required_role in {
            "video",
            "both",
        }
        result[index] = ReviewScanMembership(
            scan_id=manual_scan_id,
            role="both" if has_audio and has_video else ("audio" if has_audio else "video"),
        )
        return result
    result.append(ReviewScanMembership(scan_id=manual_scan_id, role=required_role))
    return result
