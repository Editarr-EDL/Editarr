"""Draft-only review retiming and source provenance checks."""

import os
from pathlib import Path

from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import (
    ReviewScanMembershipRow,
    SavedReviewRow,
)
from scanner.mcf import (
    TimingInterval,
    map_affine_endpoint,
    map_affine_range,
)
from scanner.models import (
    AcceptedCreditsReference,
    NewManualReviewFilter,
    ReviewFilterInput,
    ReviewRetimeRequest,
    ReviewRetimeResult,
    ReviewScanMembership,
    ScanOrigin,
)
from scanner.repository import (
    MediaRuntimeUnavailable,
    ReviewMediaUnavailable,
    ReviewMembershipError,
    ReviewRetimeError,
)

from .repository_paths import (
    resolve_local_path,
)
from .review_policy import (
    ReviewPolicyConflict,
    _map_policy_floor,
    policy_write,
    prepare_review_policy,
    validate_policy_draft,
)


@policy_write
def retime_saved_review_draft(
    db: Session, review_id: str, data: ReviewRetimeRequest
) -> ReviewRetimeResult | None:
    """Validate and retime complete draft entries without persisting the result."""
    review = db.get(SavedReviewRow, review_id)
    if review is None:
        return None
    root._check_review_revision(db, review, data.revision)
    data = data.model_copy(update={"filters": validate_policy_draft(db, review, data)})
    duration_seconds = _review_retime_runtime(db, review)
    memberships = list(data.memberships)
    membership_rows = root._validate_memberships(
        db,
        media_item_id=review.media_item_id,
        media_source_id=review.media_source_id,
        memberships=memberships,
    )
    _validate_retime_filter_provenance(
        db, review, membership_rows, data.filters, data.new_manual_filters
    )
    retime_scan_ids = {
        scan_id
        for scan_id, membership in membership_rows.items()
        if _has_unknown_cue_provenance(membership)
    }
    source = TimingInterval(
        data.current_synchronization.start_seconds,
        data.current_synchronization.end_seconds,
    )
    target = TimingInterval(
        data.proposed_synchronization.start_seconds,
        data.proposed_synchronization.end_seconds,
    )
    if source.end_seconds > duration_seconds:
        raise ReviewRetimeError(
            "invalid_synchronization_bounds",
            "Current synchronization exceeds media runtime",
            document_fields=["current_synchronization.end_seconds"],
        )
    try:
        map_affine_range(
            source.start_seconds,
            source.end_seconds,
            source,
            target,
            duration_seconds=duration_seconds,
        )
    except ValueError as exc:
        raise ReviewRetimeError(
            "invalid_synchronization_bounds",
            str(exc),
            document_fields=["proposed_synchronization.end_seconds"],
        ) from exc
    try:
        root._validate_synchronization_document(data.current_synchronization, data.mcf_document)
    except ValueError as exc:
        field = (
            "mcf_document.material_start_seconds"
            if "start" in str(exc).casefold()
            else "mcf_document.material_end_seconds"
        )
        raise ReviewRetimeError(
            "invalid_synchronization_bounds",
            str(exc),
            document_fields=[field],
        ) from exc
    try:
        mcf_document = root._document_for_synchronization(
            data.mcf_document, data.proposed_synchronization
        )
    except ValueError as exc:
        raise ReviewRetimeError(
            "invalid_synchronization_bounds",
            str(exc),
            document_fields=["mcf_document.material_end_seconds"],
        ) from exc
    filters = _map_retime_entries(
        data.filters,
        source,
        target,
        duration_seconds=duration_seconds,
        retime_scan_ids=retime_scan_ids,
        required_ids=prepare_review_policy(db, review, filters=data.filters).required_ids,
    )
    filters = validate_policy_draft(
        db,
        review,
        data.model_copy(
            update={
                "filters": filters,
                "current_synchronization": data.proposed_synchronization,
                "filter_basis": "base",
            }
        ),
        retime_result=True,
    )
    new_manual_filters = list(data.new_manual_filters)
    accepted_credits = _retime_accepted_credits(
        db,
        review,
        data.accepted_credits,
        memberships,
        data.filters,
        filters,
        retime_scan_ids,
        source,
        target,
        duration_seconds,
    )
    prepared = prepare_review_policy(db, review, filters=filters)
    return ReviewRetimeResult(
        synchronization=data.proposed_synchronization,
        mcf_document=mcf_document,
        filters=[
            ReviewFilterInput.model_validate(value.model_dump()) for value in prepared.filters
        ],
        base_filters=filters,
        policy_owner_id=prepared.owner_id,
        policy_revision=prepared.policy_revision,
        accepted_credits=accepted_credits,
        new_manual_filters=new_manual_filters,
    )


def _has_unknown_cue_provenance(membership: ReviewScanMembershipRow) -> bool:
    """A local scan with a version hash was matched by membership validation."""
    scan = membership.scan
    return scan.origin == ScanOrigin.IMPORTED.value or not scan.video_hash


def _review_retime_runtime(db: Session, review: SavedReviewRow) -> float:
    """Read a trustworthy runtime for draft retiming."""
    media_path = review.media_item.path
    if not media_path:
        raise ReviewMediaUnavailable("The review media file is unavailable")
    try:
        physical_path = Path(resolve_local_path(db, media_path).local_path).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ReviewMediaUnavailable("The review media file is unavailable") from exc
    if not physical_path.is_file() or not os.access(physical_path, os.R_OK):
        raise ReviewMediaUnavailable("The review media file is unavailable")
    try:
        duration_seconds = root.get_video_duration(str(physical_path))
    except (OSError, RuntimeError, ValueError) as exc:
        raise MediaRuntimeUnavailable("Media runtime is unavailable") from exc
    return root._default_review_synchronization(duration_seconds).end_seconds


def _validate_retime_filter_provenance(
    db: Session,
    review: SavedReviewRow,
    memberships: dict[str, ReviewScanMembershipRow],
    filters: list[ReviewFilterInput],
    new_manual_filters: list[NewManualReviewFilter],
) -> None:
    """Ensure every retimed filter still maps to its source finding."""
    ids = [value.id for value in filters] + [value.id for value in new_manual_filters]
    if len(ids) != len(set(ids)):
        raise ReviewMembershipError("Review filter IDs must be unique")
    root._filter_rows_from_inputs(db, review_id=review.id, memberships=memberships, filters=filters)


def _map_retime_entries(
    entries: list[ReviewFilterInput],
    source: TimingInterval,
    target: TimingInterval,
    *,
    duration_seconds: float,
    retime_scan_ids: set[str],
    required_ids: frozenset[str],
) -> list[ReviewFilterInput]:
    """Map cues from unverified scans and preserve other draft cue times."""
    mapped: list[ReviewFilterInput] = []
    for entry in entries:
        if entry.source_scan_id not in retime_scan_ids:
            mapped.append(entry)
            continue
        try:
            if entry.id in required_ids:
                for endpoint in (entry.start_seconds, entry.end_seconds):
                    if endpoint is not None:
                        map_affine_endpoint(
                            endpoint, source, target, duration_seconds=duration_seconds
                        )
            mapper = _map_policy_floor if entry.id in required_ids else map_affine_range
            start, end = mapper(
                entry.start_seconds,
                entry.end_seconds,
                source,
                target,
                duration_seconds=duration_seconds,
            )
            mapped.append(entry.model_copy(update={"start_seconds": start, "end_seconds": end}))
        except ReviewPolicyConflict:
            raise
        except ValueError as exc:
            raise ReviewRetimeError(
                "mapped_cue_out_of_bounds",
                str(exc),
                filter_ids=[entry.id],
            ) from exc
    return mapped


def _retime_accepted_credits(
    db: Session,
    review: SavedReviewRow,
    accepted: AcceptedCreditsReference | None,
    memberships: list[ReviewScanMembership],
    current_filters: list[ReviewFilterInput],
    mapped_filters: list[ReviewFilterInput],
    retime_scan_ids: set[str],
    source: TimingInterval,
    target: TimingInterval,
    duration_seconds: float,
) -> AcceptedCreditsReference | None:
    """Keep verified credits fixed and retime only an unknown source."""
    if accepted is None:
        return None
    scan, candidate, finding = root._validated_credits_acceptance(
        db,
        scan_id=accepted.scan_id,
        accepted_boundary_seconds=accepted.accepted_boundary_seconds,
        media_item_id=review.media_item_id,
        media_source_id=review.media_source_id,
    )
    if accepted.candidate_id != candidate.id:
        raise ReviewMembershipError("Accepted credits candidate is invalid")
    membership = next((value for value in memberships if value.scan_id == scan.id), None)
    if membership is None or membership.role not in {"video", "both"}:
        raise ReviewMembershipError("Accepted credits require a video scan membership")
    current_snapshot = root._accepted_credits_snapshot(scan, finding, accepted, current_filters)
    mapped_snapshot = next(
        (value for value in mapped_filters if value.id == current_snapshot.id), None
    )
    if mapped_snapshot is None:
        raise ReviewMembershipError("Accepted credits snapshot is invalid")
    mapped_boundary = (
        map_affine_endpoint(
            accepted.accepted_boundary_seconds,
            source,
            target,
            duration_seconds=duration_seconds,
        )
        if scan.id in retime_scan_ids
        else accepted.accepted_boundary_seconds
    )
    if mapped_snapshot.start_seconds != mapped_boundary:
        raise ReviewMembershipError("Accepted credits snapshot is invalid")
    return accepted.model_copy(update={"accepted_boundary_seconds": mapped_boundary})
