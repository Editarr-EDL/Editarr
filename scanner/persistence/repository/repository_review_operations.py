"""Saved-review previews, references, preflight, and sidecar exports."""

import math
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import (
    CommunityOperationRow,
    MediaItemExportRow,
    MediaItemExportScanRow,
    MediaItemRow,
    ReviewAcceptedCreditsRow,
    ReviewFilterRow,
    ReviewPolicyEvidenceRow,
    ReviewScanMembershipRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanRow,
    UserRow,
)
from scanner.models import (
    CreditsDetectionResult,
    EditAction,
    ExportFormat,
    ExportScanReference,
    McfPreflight,
    ReviewExportResponse,
    ReviewReference,
    ScanReferences,
)
from scanner.repository import (
    CreditsRouteError,
    IncompleteMcfReview,
    ReviewMembershipError,
    ReviewRevisionConflict,
)

from .repository_paths import (
    resolve_local_path,
)
from .repository_review_exports import publish_review_sidecar
from .repository_rows import (
    _utc,
)
from .review_export_target import _review_export_scans as _review_export_scans
from .review_policy import (
    policy_write,
    prepare_review_policy,
    require_policy_evidence,
)
from .review_version_evidence import review_file_matches
from .scan_jobs import ScanMedia


def _referencing_reviews(db: Session, scan_id: str):
    memberships = select(ReviewScanMembershipRow.review_id).where(
        ReviewScanMembershipRow.scan_id == scan_id
    )
    copied_filters = select(ReviewFilterRow.review_id).where(
        ReviewFilterRow.source_scan_id == scan_id
    )
    accepted_credits = select(ReviewAcceptedCreditsRow.review_id).where(
        ReviewAcceptedCreditsRow.scan_id == scan_id
    )
    direct = set(db.scalars(memberships.union(copied_filters, accepted_credits)))
    history = (
        select(ReviewPolicyEvidenceRow.review_id)
        .join(ScanFindingRow, ScanFindingRow.id == ReviewPolicyEvidenceRow.source_finding_id)
        .where(ScanFindingRow.scan_id == scan_id)
    )
    from .review_policy import owner_policy
    from .review_policy_evidence import required_evidence

    candidates = db.scalars(
        select(SavedReviewRow).where(SavedReviewRow.id.in_(direct) | SavedReviewRow.id.in_(history))
    )
    references = [
        review.id
        for review in candidates
        if review.id in direct
        or any(
            value.source_scan_id == scan_id
            for value in required_evidence(
                db, review, owner_policy(db, review.owner_id), review.synchronization
            )
        )
    ]
    return SavedReviewRow.id.in_(references)


def get_scan_references(db: Session, scan_id: str) -> ScanReferences | None:
    """Find review, export, and caller-safe retention blockers for scan deletion."""
    scan = db.get(ScanRow, scan_id)
    if scan is None:
        return None
    references = [
        ReviewReference(id=row.id, name=row.name, updated_at=_utc(row.updated_at))
        for row in db.query(SavedReviewRow)
        .filter(_referencing_reviews(db, scan_id))
        .order_by(SavedReviewRow.updated_at.desc(), SavedReviewRow.id)
        .all()
    ]
    active_exports = sorted(
        {
            export_format
            for (export_format,) in db.query(MediaItemExportRow.format)
            .join(MediaItemExportScanRow)
            .filter(MediaItemExportScanRow.scan_id == scan_id)
            .all()
        },
        key=lambda export_format: export_format.value,
    )
    return ScanReferences(
        scan_id=scan_id,
        active_exports=active_exports,
        reviews=references,
        community_in_progress=db.query(CommunityOperationRow.operation_id)
        .filter(
            CommunityOperationRow.scan_id == scan_id,
            CommunityOperationRow.state.in_(("pending", "submitted")),
        )
        .first()
        is not None,
    )


@dataclass(frozen=True)
class ScanReferenceFacts:
    """A referencing review's owner and target, independent of its source scan."""

    owner_id: int | None
    owner_role: str | None
    media: ScanMedia


def scan_reference_facts(db: Session, scan_id: str) -> dict[str, ScanReferenceFacts]:
    """Read each review's own authorization target without filtering deletion blockers."""
    rows = (
        db.query(SavedReviewRow.id, UserRow.id, UserRow.role, MediaItemRow)
        .join(MediaItemRow, MediaItemRow.id == SavedReviewRow.media_item_id)
        .outerjoin(UserRow, UserRow.id == SavedReviewRow.owner_id)
        .filter(_referencing_reviews(db, scan_id))
        .all()
    )
    references = {}
    for review_id, owner_id, role, media in rows:
        try:
            path = resolve_local_path(db, media.path).local_path if media.path else ""
        except ValueError:
            path = ""
        references[review_id] = ScanReferenceFacts(
            owner_id, role, ScanMedia(media.server_id, media.jellyfin_id, media.library_id, path)
        )
    return references


def render_saved_review_filter_preview(
    db: Session, review_id: str, filter_id: str, *, preview_kind: str
) -> bytes | None:
    """Render a short media preview using the same effective review as formats."""
    row = db.get(SavedReviewRow, review_id)
    if row is None:
        return None
    prepared = prepare_review_policy(db, row)
    require_policy_evidence(prepared)
    review_filter = next((value for value in prepared.filters if value.id == filter_id), None)
    if review_filter is None:
        return None
    scan = db.get(ScanRow, review_filter.source_scan_id)
    membership = next(
        (value for value in row.memberships if value.scan_id == review_filter.source_scan_id),
        None,
    )
    if (
        scan is None
        or membership is None
        or not root._scan_is_available(
            db,
            scan,
            media_item_id=row.media_item_id,
            media_source_id=row.media_source_id,
        )
    ):
        raise ValueError("The review filter source scan is unavailable")
    if row.media_item.path:
        target_path = Path(resolve_local_path(db, row.media_item.path).local_path).resolve()
        if not root._scan_matches_physical_version(scan, target_path):
            raise ValueError("The review filter source media is unavailable")
    if review_file_matches(db, review_id, scan.resolved_local_path) is False:
        raise ReviewMembershipError("The original Editarr review version is unavailable")
    if preview_kind == "mute":
        if review_filter.source != "audio" or review_filter.edit_action != EditAction.MUTE.value:
            raise ValueError("Only audio mute filters can be previewed")
    elif preview_kind == "video":
        if review_filter.source != "visual" or review_filter.edit_action != EditAction.SKIP.value:
            raise ValueError("Only video skip filters can be previewed")
    else:
        raise ValueError("Unknown review preview kind")
    start_seconds = max(0.0, review_filter.start_seconds)
    end_seconds = max(start_seconds, review_filter.end_seconds or (start_seconds + 1.0))
    if scan.duration_seconds is not None:
        end_seconds = min(end_seconds, scan.duration_seconds)
    if preview_kind == "mute":
        return root.render_mute_preview(scan.resolved_local_path, start_seconds, end_seconds)
    return root.render_video_preview(scan.resolved_local_path, start_seconds, end_seconds)


def render_credits_detection_preview(
    db: Session, *, scan_id: str, boundary_seconds: float
) -> bytes | None:
    """Render a candidate clip from the verified credits media file."""
    scan = db.get(ScanRow, scan_id)
    if scan is None:
        return None
    readiness_failure = root._credits_readiness_failure(scan)
    if readiness_failure is not None:
        code, message, status_code = readiness_failure
        raise CreditsRouteError(code, message, status_code)
    if scan.duration_seconds is None:
        raise CreditsRouteError("media_runtime_unavailable", "Media runtime is unavailable", 409)
    result = CreditsDetectionResult.model_validate_json(scan.credits_detection.result_json)
    if result.outcome != "candidate" or result.best_candidate_id is None:
        raise CreditsRouteError(
            "credits_detection_not_ready",
            "Credits detection has no candidate to preview",
            409,
        )
    if (
        not math.isfinite(boundary_seconds)
        or boundary_seconds < 0
        or boundary_seconds >= scan.duration_seconds
    ):
        raise CreditsRouteError(
            "invalid_credits_boundary",
            "Credits preview boundary is outside the media runtime",
            422,
        )
    media_path = root._validated_credits_media_path(db, scan)
    preview_start = max(0.0, boundary_seconds - 6.0)
    preview_end = min(scan.duration_seconds, boundary_seconds + 6.0)
    try:
        content = root.render_video_preview(media_path, preview_start, preview_end)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CreditsRouteError(
            "credits_clip_failed", "Credits preview could not be rendered", 500
        ) from exc
    if len(content) > 16 * 1024 * 1024:
        raise CreditsRouteError(
            "credits_clip_too_large", "Credits preview exceeds the size limit", 413
        )
    return content


def preflight_saved_review_mcf(
    db: Session, review_id: str, *, prepared=None
) -> McfPreflight | None:
    """Report metadata and filter issues that block strict MCF export."""
    review = db.get(SavedReviewRow, review_id)
    if review is None:
        return None
    prepared = prepared or prepare_review_policy(db, review)
    require_policy_evidence(prepared)
    _memberships, scans, _physical_path = _review_export_scans(db, review)
    from .community_import_targets import review_duration

    known_durations = [
        duration
        for scan in scans.values()
        if (duration := review_duration(scan, review.media_item_id, review.media_source_id))
        is not None
    ]
    issues = root._mcf_document_issues(
        review.mcf_document, scans.values(), durations=known_durations
    )
    duration_seconds = min(known_durations) if known_durations else None
    for position, review_filter in enumerate(prepared.filters):
        if review_filter.selected:
            issues.extend(root._mcf_filter_issues(review_filter, position, duration_seconds))
    return McfPreflight(
        review_id=review.id,
        revision=review.revision,
        eligible=not issues,
        issues=issues,
        policy_owner_id=prepared.owner_id,
        policy_revision=prepared.policy_revision,
    )


@policy_write
def export_saved_review_mcf(
    db: Session,
    review_id: str,
    revision: int,
    *,
    policy_revision: int | None = None,
    policy_owner_id: int | None = None,
) -> ReviewExportResponse | None:
    """Validate and publish an MCF sidecar at the requested review revision."""
    from .review_export_content import prepare_review_export

    output = prepare_review_export(
        db,
        review_id,
        revision,
        ExportFormat.MCF,
        policy_revision=policy_revision,
        policy_owner_id=policy_owner_id,
    )
    if output is None:
        return None
    review, prepared = output.review, output.policy
    export = publish_review_sidecar(
        db,
        review=review,
        memberships=list(output.memberships),
        destination=output.media.with_suffix(".mcf"),
        content=output.content,
        export_format=ExportFormat.MCF,
        content_revision=review.mcf_content_revision,
        policy=prepared,
        pass_through_errors=(ReviewRevisionConflict, ReviewMembershipError, IncompleteMcfReview),
    )
    return ReviewExportResponse(
        format=ExportFormat.MCF,
        review_id=review.id,
        exported_format_revision=export.exported_format_revision,
        exported_at=_utc(export.exported_at),
        exported_scans=[
            ExportScanReference(
                id=link.scan.id,
                scan_id=link.scan.scan_number,
                role=link.role,
                name=link.scan.name,
                origin=link.scan.origin,
                community_provenance=root._scan_from_row(link.scan).community_provenance,
            )
            for link in export.scans
        ],
        exported_review=root._review_summary(db, review),
        policy_owner_id=prepared.owner_id,
        policy_revision=prepared.policy_revision,
    )


@policy_write
def export_saved_review_edl(
    db: Session,
    review_id: str,
    revision: int,
    *,
    policy_revision: int | None = None,
    policy_owner_id: int | None = None,
) -> ReviewExportResponse | None:
    """Validate and publish an EDL sidecar at the requested review revision."""
    from .review_export_content import prepare_review_export

    output = prepare_review_export(
        db,
        review_id,
        revision,
        ExportFormat.EDL,
        policy_revision=policy_revision,
        policy_owner_id=policy_owner_id,
    )
    if output is None:
        return None
    review, prepared = output.review, output.policy
    export = publish_review_sidecar(
        db,
        review=review,
        memberships=list(output.memberships),
        destination=output.media.with_suffix(".edl"),
        content=output.content,
        export_format=ExportFormat.EDL,
        content_revision=review.edl_content_revision,
        policy=prepared,
        pass_through_errors=(ReviewRevisionConflict, ReviewMembershipError),
    )
    summary = root._review_summary(db, review)
    return ReviewExportResponse(
        format=ExportFormat.EDL,
        review_id=review.id,
        exported_format_revision=export.exported_format_revision,
        exported_at=_utc(export.exported_at),
        exported_scans=sorted(
            (
                ExportScanReference(
                    id=link.scan.id,
                    scan_id=link.scan.scan_number,
                    role=link.role,
                    name=link.scan.name,
                    origin=link.scan.origin,
                    community_provenance=root._scan_from_row(link.scan).community_provenance,
                )
                for link in export.scans
            ),
            key=lambda reference: reference.scan_id,
        ),
        exported_review=summary,
        policy_owner_id=prepared.owner_id,
        policy_revision=prepared.policy_revision,
    )
