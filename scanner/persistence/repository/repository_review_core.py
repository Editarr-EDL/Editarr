"""Shared saved-review row conversion and membership validation."""

import os
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import (
    MediaItemRow,
    ReviewAcceptedCreditsRow,
    ReviewFilterRow,
    ReviewScanMembershipRow,
    ReviewSynchronizationRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanMediaItemAssociationRow,
    ScanRow,
)
from scanner.models import (
    AcceptedCreditsProvenance,
    ReviewFilter,
    ReviewFilterInput,
    ReviewMcfDocument,
    ReviewScanMembership,
    ReviewScanReference,
    ReviewSynchronization,
    SavedReview,
    SavedReviewSummary,
)
from scanner.repository import (
    CreditsRouteError,
    ReviewMembershipError,
)

from .community_import_targets import (
    project_review_channel,
    review_duration,
    validate_target_role,
    verified_both_channel,
    verified_target,
)
from .repository_exports import (
    _review_summary,
    target_export_rows,
)
from .repository_rows import (
    _scan_from_row,
    _utc,
)
from .review_community_sources import review_sources
from .review_filter_tracking import label_edits, load_source_findings, with_label_provenance
from .review_policy import prepare_review_policy
from .review_policy_display import locked_severities
from .review_repairs import repair_records_by_review
from .review_scan_paths import review_scan_location
from .version_reuse import bound_scan_file_matches, source_pending_reuse


def _review_filter(row: ReviewFilterRow) -> ReviewFilter:
    """Convert a stored filter and its source metadata to the API review filter."""
    return ReviewFilter.model_validate(row, from_attributes=True)


def _review_detail(
    db: Session,
    row: SavedReviewRow,
    *,
    source: str | None = None,
    item_id: str | None = None,
) -> SavedReview:
    """Build a full review response with memberships, filters, and export state."""
    from .review_repairs import require_repaired

    require_repaired(db, row.id)
    summary = _review_summary(db, row, source=source, item_id=item_id)
    # Batch-load source findings once; policy and lock evaluation then reuse them.
    sources = load_source_findings(db, row)
    prepared = prepare_review_policy(db, row)
    edits = label_edits(row, sources)
    detail = SavedReview(
        **summary.model_dump(),
        memberships=[
            ReviewScanMembership(scan_id=membership.scan_id, role=membership.role)
            for membership in row.memberships
        ],
        scans=[
            ReviewScanReference(scan=_scan_from_row(membership.scan), role=membership.role)
            for membership in row.memberships
        ],
        filters=with_label_provenance(prepared.filters, edits),
        base_filters=with_label_provenance(prepared.base_filters, edits),
        policy_owner_id=prepared.owner_id,
        policy_revision=prepared.policy_revision,
        policy_missing_evidence=list(prepared.missing_evidence),
        policy_required_filter_ids=sorted(prepared.required_ids),
        policy_locked_severities=locked_severities(db, prepared),
        owner_id=row.owner_id,
        original_author_id=row.original_author_id,
        mcf_document=(
            ReviewMcfDocument.model_validate(row.mcf_document, from_attributes=True)
            if row.mcf_document is not None
            else ReviewMcfDocument()
        ),
        synchronization=_review_synchronization(row.synchronization),
        accepted_credits=_accepted_credits_provenance(row.accepted_credits),
        community_sources=review_sources(db, row),
    )
    del sources  # held until here so the identity map keeps the batched findings
    return detail


def _review_synchronization(row: ReviewSynchronizationRow | None) -> ReviewSynchronization:
    if row is None:
        raise ValueError("Review synchronization is unavailable")
    return ReviewSynchronization.model_validate(row, from_attributes=True)


def _accepted_credits_provenance(
    row: ReviewAcceptedCreditsRow | None,
) -> AcceptedCreditsProvenance | None:
    """Expose the stored accepted credits source and boundary for a review."""
    if row is None:
        return None
    return AcceptedCreditsProvenance(
        scan_id=row.scan_id,
        source_finding_id=row.source_finding_id,
        candidate_id=row.candidate_id,
        detected_boundary_seconds=row.detected_boundary_seconds,
        accepted_boundary_seconds=row.accepted_boundary_seconds,
        confidence=row.confidence,
        method=row.method,
        accepted_at=_utc(row.accepted_at),
    )


def _scan_is_available(
    db: Session, scan: ScanRow, *, media_item_id: str, media_source_id: str
) -> bool:
    """Check whether a completed scan belongs to the requested media version."""
    if source_pending_reuse(scan):
        return False
    if scan.community_import is not None and scan.community_import.artifact_bytes:
        return (
            scan.status == "complete"
            and verified_target(db, scan, media_item_id, media_source_id) is not None
        )
    return (
        scan.status == "complete"
        and scan.media_source_id == (media_source_id or None)
        and (
            scan.media_item_id == media_item_id
            or db.query(ScanMediaItemAssociationRow)
            .filter(
                ScanMediaItemAssociationRow.scan_id == scan.id,
                ScanMediaItemAssociationRow.media_item_id == media_item_id,
            )
            .first()
            is not None
        )
    )


def _resolved_scan_path(scan: ScanRow) -> Path:
    return Path(scan.resolved_local_path).resolve()


def _compute_video_hash(path: str | Path) -> str | None:
    try:
        return root.compute_video_md5(str(path))
    except OSError:
        return None


def _scan_matches_physical_version(
    scan: ScanRow, target_path: Path, *, require_hash: bool = False
) -> bool:
    """Check whether a scan still belongs to the requested physical media version."""
    approval = bound_scan_file_matches(scan, target_path)
    if approval is not None:
        return approval
    if _resolved_scan_path(scan) != target_path.resolve():
        return False
    if scan.video_hash is None:
        return not require_hash
    try:
        return root.compute_video_md5(str(target_path)) == scan.video_hash
    except OSError:
        return False


def _validated_credits_media_path(db: Session, scan: ScanRow) -> Path:
    """Resolve readable credits media and verify its physical file hash."""
    if not scan.media_item.path:
        raise CreditsRouteError("credits_media_unavailable", "Credits media is unavailable", 503)
    try:
        media_path = Path(root.resolve_local_path(db, scan.media_item.path).local_path).resolve(
            strict=True
        )
    except (OSError, ValueError) as exc:
        raise CreditsRouteError(
            "credits_media_unavailable", "Credits media is unavailable", 503
        ) from exc
    if not media_path.is_file() or not os.access(media_path, os.R_OK):
        raise CreditsRouteError("credits_media_unavailable", "Credits media is unavailable", 503)
    if not _scan_matches_physical_version(scan, media_path, require_hash=True):
        raise CreditsRouteError(
            "credits_target_mismatch",
            "Credits detection does not match the physical media file",
            409,
        )
    return media_path


def _validate_memberships(
    db: Session,
    *,
    media_item_id: str,
    media_source_id: str,
    memberships: list[ReviewScanMembership],
) -> dict[str, ReviewScanMembershipRow]:
    """Validate review scan roles and confirm one physical target version."""
    if len({membership.scan_id for membership in memberships}) != len(memberships):
        raise ReviewMembershipError("A review cannot include the same scan twice")
    media = db.get(MediaItemRow, media_item_id)
    target_path = None
    if media is not None and media.path:
        target_path = Path(root.resolve_local_path(db, media.path).local_path).resolve()
    rows: dict[str, ReviewScanMembershipRow] = {}
    physical_path: Path | None = None
    for position, membership in enumerate(memberships):
        if membership.role not in {"audio", "video", "both"}:
            raise ReviewMembershipError("Invalid review scan role")
        scan = db.get(ScanRow, membership.scan_id)
        if scan is None or not _scan_is_available(
            db, scan, media_item_id=media_item_id, media_source_id=media_source_id
        ):
            raise ReviewMembershipError(
                "Selected scans must be complete and match the media version"
            )
        if membership.role in {"audio", "both"} and not scan.scan_audio:
            raise ReviewMembershipError("Selected scan does not include audio analysis")
        if membership.role in {"video", "both"} and not scan.scan_video:
            raise ReviewMembershipError("Selected scan does not include video analysis")
        association = verified_target(db, scan, media_item_id, media_source_id)
        validate_target_role(association, membership.role)
        scan_path, matches = review_scan_location(scan, target_path, association)
        if target_path is not None and scan_path != target_path:
            raise ReviewMembershipError(
                "Selected scans must resolve to the target physical media file"
            )
        if not matches:
            raise ReviewMembershipError(
                "Selected scans must match the target physical media version"
            )
        if physical_path is None:
            physical_path = scan_path
        elif scan_path != physical_path:
            raise ReviewMembershipError(
                "Selected scans must resolve to the same physical media file"
            )
        rows[membership.scan_id] = ReviewScanMembershipRow(
            scan_id=membership.scan_id,
            role=membership.role,
            position=position,
            scan=scan,
        )
    return rows


def _snapshot_findings(
    db: Session,
    memberships: dict[str, ReviewScanMembershipRow],
    *,
    media_item_id: str | None = None,
    media_source_id: str | None = None,
) -> list[ReviewFilterRow]:
    """Copy selected scan findings into review filter inputs."""
    snapshots: list[ReviewFilterRow] = []
    for scan_id, membership in memberships.items():
        sources = (
            {"audio", "visual"}
            if membership.role == "both"
            else {"audio" if membership.role == "audio" else "visual"}
        )
        condition = ScanFindingRow.source.in_(sources)
        if (
            membership.scan.community_import is not None
            and membership.scan.community_import.artifact_bytes
        ):
            channels = (
                {"audio", "video", "both"}
                if membership.role == "both"
                else {membership.role, "both"}
            )
            condition = ScanFindingRow.mcf_channel.in_(channels)
        findings = (
            db.query(ScanFindingRow)
            .filter(ScanFindingRow.scan_id == scan_id, condition)
            .order_by(ScanFindingRow.start_seconds, ScanFindingRow.id)
            .all()
        )
        association = (
            verified_target(db, membership.scan, media_item_id, media_source_id)
            if media_item_id and findings
            else None
        )
        for finding in findings:
            snapshots.append(
                ReviewFilterRow(
                    id=str(uuid.uuid4()),
                    source_scan_id=scan_id,
                    source_finding_id=finding.id,
                    position=len(snapshots),
                    source=finding.source,
                    category=finding.category,
                    subcategory=finding.subcategory,
                    severity=finding.severity,
                    mcf_channel=project_review_channel(
                        association, finding.mcf_channel, membership.role
                    ),
                    mcf_comment=finding.mcf_comment,
                    label=finding.label,
                    details=finding.details,
                    group_key=finding.group_key,
                    start_seconds=finding.start_seconds,
                    end_seconds=finding.end_seconds,
                    confidence=finding.confidence,
                    edit_action=finding.edit_action,
                    selected=finding.selected,
                )
            )
    return snapshots


def list_saved_reviews(
    db: Session,
    *,
    media_item_id: str,
    media_source_id: str | None = None,
    sort: str = "updated_at",
    direction: str = "desc",
    target_source: str | None = None,
    target_item_id: str | None = None,
    review_ids: list[str] | None = None,
) -> list[SavedReviewSummary]:
    """List saved reviews for one target with current export summaries."""
    query = db.query(SavedReviewRow).filter(SavedReviewRow.media_item_id == media_item_id)
    query = query.filter(SavedReviewRow.media_source_id == (media_source_id or ""))
    if review_ids is not None:
        query = query.filter(SavedReviewRow.id.in_(review_ids))
    if sort == "name":
        rows = query.all()
        # Python's Unicode casefolding is the contract.  SQLite's built-in
        # collations and lower() are ASCII-oriented and cannot implement it.
        rows.sort(key=lambda row: row.id)
        rows.sort(key=lambda row: row.name.casefold(), reverse=direction == "desc")
    else:
        primary_order = (
            SavedReviewRow.updated_at.asc()
            if direction == "asc"
            else SavedReviewRow.updated_at.desc()
        )
        rows = query.order_by(primary_order, SavedReviewRow.id.asc()).all()
    if not rows:
        return []
    export_rows = target_export_rows(db, media_item_id, media_source_id or "")
    repairs = repair_records_by_review(db, [row.id for row in rows])
    return [
        _review_summary(
            db,
            row,
            source=target_source,
            item_id=target_item_id,
            export_rows=export_rows,
            repair_records=repairs[row.id],
        )
        for row in rows
    ]


def get_saved_review(
    db: Session,
    review_id: str,
    *,
    target_source: str | None = None,
    target_item_id: str | None = None,
) -> SavedReview | None:
    """Load a saved review and its related scans, filters, and export state."""
    row = db.get(SavedReviewRow, review_id)
    return (
        None
        if row is None
        else _review_detail(db, row, source=target_source, item_id=target_item_id)
    )


def _filter_rows_from_inputs(
    db: Session,
    *,
    review_id: str,
    memberships: dict[str, ReviewScanMembershipRow],
    filters: list[ReviewFilterInput],
) -> list[ReviewFilterRow]:
    """Validate filter inputs against memberships and build storage rows."""
    from .review_filter_tracking import cue_provenance, source_cue_flags

    valid_ids = {scan_id for scan_id in memberships}
    if len({value.id for value in filters}) != len(filters):
        raise ReviewMembershipError("Review filter IDs must be unique")
    provenance = [
        (value.source_scan_id, value.source_finding_id)
        for value in filters
        if value.source_finding_id is not None
    ]
    if len(set(provenance)) != len(provenance):
        raise ReviewMembershipError("Review filter source provenance must be unique")
    existing = {
        row.id: row
        for row in db.query(ReviewFilterRow).filter(ReviewFilterRow.review_id == review_id).all()
    }
    by_provenance = {
        cue_provenance(row): row for row in existing.values() if row.source_finding_id is not None
    }
    review = db.get(SavedReviewRow, review_id)
    durations = {
        scan_id: review_duration(membership.scan, review.media_item_id, review.media_source_id)
        if review is not None
        else membership.scan.duration_seconds
        for scan_id, membership in memberships.items()
    }
    rows: list[ReviewFilterRow] = []
    for position, value in enumerate(filters):
        if value.source_scan_id not in valid_ids:
            raise ReviewMembershipError("Review filters must reference a review membership")
        previous = existing.get(value.id)
        provenance = (value.source_scan_id, value.source_finding_id, value.source)
        if previous is not None and provenance != (
            previous.source_scan_id,
            previous.source_finding_id,
            previous.source,
        ):
            raise ReviewMembershipError("Review filter provenance is immutable")
        membership = memberships[value.source_scan_id]
        source_scan = db.get(ScanRow, value.source_scan_id)
        if source_scan is None:
            raise ReviewMembershipError("Review filter source scan is unavailable")
        recovered_manual = (
            value.source_finding_id is None
            and previous is not None
            and source_scan.origin == "manual"
        )
        if value.source_finding_id is None and not recovered_manual:
            raise ReviewMembershipError("Review filter provenance is required")
        finding = (
            db.query(ScanFindingRow)
            .filter(
                ScanFindingRow.id == value.source_finding_id,
                ScanFindingRow.scan_id == value.source_scan_id,
            )
            .one_or_none()
        )
        if not recovered_manual and (finding is None or finding.source != value.source):
            raise ReviewMembershipError("Review filter provenance is invalid")
        if review is not None:
            validate_target_role(
                verified_target(db, source_scan, review.media_item_id, review.media_source_id),
                value.mcf_channel or ("audio" if value.source == "audio" else "video"),
            )
        if recovered_manual or not verified_both_channel(source_scan, finding):
            if value.source == "audio" and membership.role not in {"audio", "both"}:
                raise ReviewMembershipError("Audio filters require an audio membership")
            if value.source == "visual" and membership.role not in {"video", "both"}:
                raise ReviewMembershipError("Visual filters require a video membership")
        if (
            durations[value.source_scan_id] is not None
            and value.end_seconds is not None
            and value.end_seconds > durations[value.source_scan_id]
        ):
            raise ValueError("Review filter range exceeds media duration")
        prior_cue = previous or by_provenance.get(cue_provenance(value))
        if prior_cue is not None:
            added, edited = prior_cue.manually_added, prior_cue.manually_edited
        else:
            added, edited = source_cue_flags(source_scan, finding)
        rows.append(
            ReviewFilterRow(
                review_id=review_id,
                id=value.id,
                source_scan_id=value.source_scan_id,
                source_finding_id=value.source_finding_id,
                position=position,
                source=value.source,
                category=value.category,
                label=value.label,
                details=value.details,
                subcategory=value.subcategory,
                severity=value.severity,
                mcf_channel=value.mcf_channel,
                mcf_comment=value.mcf_comment,
                group_key=value.group_key,
                start_seconds=value.start_seconds,
                end_seconds=value.end_seconds,
                confidence=value.confidence,
                edit_action=value.edit_action.value,
                selected=value.selected,
                manually_added=added,
                manually_edited=edited,
            )
        )
    return rows
