"""One effective-review preparation contract for downloads and sidecar publication."""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import ReviewScanMembershipRow, SavedReviewRow
from scanner.formats.edl import export_edl_content
from scanner.mcf import McfCueEntry, export_mcf_content
from scanner.models import (
    EditAction,
    ExportFormat,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    ReviewMcfDocument,
    ScanFinding,
    Severity,
)

from . import review_community_export as community_export
from .repository_mcf_preflight import _signature_value, filter_mcf_value
from .review_community_sources import export_runtime
from .review_community_sources import review_filters as community_filters
from .review_export_target import _review_export_scans
from .review_policy import (
    PreparedReviewPolicy,
    prepare_review_policy,
    require_policy_evidence,
    require_policy_revision,
)
from .review_policy_evidence import policy_export_filters, policy_export_interval


@dataclass(frozen=True)
class PreparedReviewExport:
    review: SavedReviewRow
    policy: PreparedReviewPolicy
    memberships: tuple[ReviewScanMembershipRow, ...]
    media: Path
    format: ExportFormat
    format_revision: int
    content: bytes
    # The runtime exports were clipped to; retained snapshots reuse it for community filters.
    duration_seconds: float | None = None


def prepare_review_export(
    db: Session,
    review_id: str,
    revision: int,
    export_format: ExportFormat,
    *,
    policy_revision: int | None = None,
    policy_owner_id: int | None = None,
) -> PreparedReviewExport | None:
    """Prepare exact bytes under the writer lock without applying personal defaults."""
    from .repository_review_operations import preflight_saved_review_mcf

    review = db.get(SavedReviewRow, review_id)
    if review is None:
        return None
    root._check_review_revision(db, review, revision)
    policy = prepare_review_policy(db, review)
    require_policy_revision(policy, policy_revision, policy_owner_id)
    require_policy_evidence(policy)
    memberships, scans, media = _review_export_scans(db, review)
    duration = export_runtime(db, review, list(scans.values()), lambda: _probed_duration(media))
    if export_format == ExportFormat.MCF:
        preflight = preflight_saved_review_mcf(db, review_id, prepared=policy)
        if not preflight.eligible:
            raise root.IncompleteMcfReview(preflight)
    content, format_revision = render_review_export(
        review,
        policy,
        export_format,
        duration,
        community_filters(review),
        severities=policy.policy.severities,
    )
    return PreparedReviewExport(
        review,
        policy,
        tuple(memberships),
        media,
        export_format,
        format_revision,
        content,
        duration,
    )


def render_review_export(review, policy, export_format, duration, community=(), *, severities=None):
    """Render prepared live or retained review values through the same format serializers.

    `community` holds the stored community filter snapshots the review includes;
    `severities` are the review owner's enforced severities, which apply to them too.
    """
    filters = policy_export_filters(policy, duration_seconds=duration)
    community = community_export.exportable(
        community, severities=severities, duration_seconds=duration
    )
    if export_format == ExportFormat.MCF:
        document = ReviewMcfDocument.model_validate(review.mcf_document, from_attributes=True)
        if policy.required_ids:
            start, end = policy_export_interval(
                document.material_start_seconds,
                document.material_end_seconds,
                duration_seconds=duration,
            )
            document = document.model_copy(
                update={"material_start_seconds": start, "material_end_seconds": end}
            )
        entries = [
            McfCueEntry(
                cue_index=position,
                payload_index=0,
                line_number=position + 1,
                start_seconds=review_filter.start_seconds,
                end_seconds=review_filter.end_seconds,
                category=McfCategoryFamily(_signature_value(review_filter.category)),
                subcategory=McfCategory(review_filter.subcategory),
                severity=Severity(_signature_value(review_filter.severity)),
                mcf_channel=McfChannel(filter_mcf_value(review_filter, "mcf_channel")),
                mcf_comment=filter_mcf_value(review_filter, "mcf_comment"),
            )
            for position, review_filter in enumerate(filters)
            if review_filter.selected
        ]
        entries.extend(community_export.mcf_entries(community, len(entries)))
        content = export_mcf_content(document, entries)
        format_revision = review.mcf_content_revision
    else:
        findings = [
            ScanFinding(
                id=position,
                scan_id=review_filter.source_scan_id,
                **{
                    field: getattr(review_filter, field)
                    for field in (
                        "category",
                        "subcategory",
                        "severity",
                        "mcf_channel",
                        "mcf_comment",
                        "start_seconds",
                        "end_seconds",
                        "confidence",
                        "label",
                        "details",
                        "group_key",
                        "source",
                        "selected",
                    )
                },
                edit_action=EditAction(review_filter.edit_action),
            )
            for position, review_filter in enumerate(
                (value for value in filters if value.selected), 1
            )
        ]
        findings.extend(community_export.edl_findings(community, len(findings) + 1))
        content = export_edl_content(findings).encode("utf-8")
        format_revision = review.edl_content_revision
    return content, format_revision


def _probed_duration(media: Path) -> float | None:
    """The runtime of a community-only review's media file when no scan records one."""
    try:
        seconds = root.get_video_duration(str(media))
    except (OSError, RuntimeError, ValueError):
        return None
    return seconds if seconds and seconds > 0 else None
