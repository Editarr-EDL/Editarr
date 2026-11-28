"""Persist parsed EDL actions as immutable evidence and an editable review."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import (
    MediaItemRow,
    ReviewFilterRow,
    ReviewScanMembershipRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.formats.edl import ParsedEdl
from scanner.modeling.model_videoskip import VideoSkipSource
from scanner.models import (
    EdlImportResult,
    EdlImportWarning,
    ReviewFilterInput,
    ReviewMcfDocument,
)

from .filter_preferences import base_filter_settings
from .import_metadata import prepare_edl_review_document
from .import_transaction import (
    refresh_imported_rows,
    rollback_on_import_error,
    stage_imported_review,
    stage_imported_scan,
)
from .repository_rows import _scan_from_row
from .review_policy import policy_write, prepare_review_policy
from .review_policy_evidence import bind_review_evidence
from .version_reuse import stage_reuse_source


@policy_write
def import_edl_as_saved_review(
    db: Session,
    *,
    media_item: MediaItemRow,
    resolved_local_path: str,
    media_source_id: str | None,
    source_name: str,
    review_name: str,
    parsed: ParsedEdl,
    duration_seconds: float | None = None,
    catalog_mcf_document: ReviewMcfDocument | None = None,
    imported_at: datetime | None = None,
    verification_version_id: str | None = None,
    videoskip_source: VideoSkipSource | None = None,
) -> EdlImportResult:
    """Import parsed EDL actions into a scan and saved review for the target media."""
    if not parsed.filters:
        raise ValueError("No supported EDL actions were found")
    source_id = media_source_id or None
    next_number = (
        db.query(func.max(ScanRow.scan_number))
        .filter(ScanRow.media_item_id == media_item.id)
        .scalar()
        or 0
    ) + 1
    imported_at = imported_at or datetime.now(timezone.utc)
    has_audio = any(item.source == "audio" or item.mcf_channel == "both" for item in parsed.filters)
    has_video = any(item.source == "visual" for item in parsed.filters)
    scan = stage_imported_scan(
        db,
        media_item=media_item,
        scan_number=next_number,
        review_name=review_name,
        source_name=source_name,
        imported_at=imported_at,
        resolved_local_path=resolved_local_path,
        media_source_id=source_id,
        duration_seconds=(
            catalog_mcf_document.material_end_seconds
            if catalog_mcf_document is not None and videoskip_source is None
            else None
        ),
        has_audio=has_audio,
        has_video=has_video,
        progress_message="Imported VideoSkip" if videoskip_source else "Imported EDL",
    )
    finding_rows: list[ScanFindingRow] = []
    for item in parsed.filters:
        finding = ScanFindingRow(
            scan_id=scan.id,
            category=item.category,
            subcategory=item.subcategory,
            severity=item.severity,
            mcf_channel=item.mcf_channel,
            mcf_comment=item.mcf_comment,
            start_seconds=item.start_seconds,
            end_seconds=item.end_seconds,
            confidence=item.confidence,
            label=item.label,
            details=item.details,
            source=item.source,
            selected=item.selected,
            edit_action=item.edit_action.value,
            group_key=item.group_key,
        )
        db.add(finding)
        finding_rows.append(finding)
    db.flush()
    warning = (
        EdlImportWarning(
            code="unsupported_actions_ignored",
            message="Unsupported EDL actions were ignored and not imported",
            ignored_action_count=parsed.ignored_action_count,
        )
        if parsed.ignored_action_count
        else None
    )
    if verification_version_id:
        stage_reuse_source(db, scan, verification_version_id, videoskip_source=videoskip_source)
        db.commit()
        return EdlImportResult(
            review=None, scan=_scan_from_row(scan, finding_count=len(finding_rows)),
            warning=warning,
        )
    review = stage_imported_review(
        db,
        media_item=media_item,
        media_source_id=source_id,
        review_name=review_name,
        imported_at=imported_at,
    )
    review.synchronization = root._review_synchronization_row(
        review.id, root._default_review_synchronization(duration_seconds)
    )
    role = "both" if has_audio and has_video else ("audio" if has_audio else "video")
    membership = ReviewScanMembershipRow(
        review_id=review.id,
        scan_id=scan.id,
        role=role,
        position=0,
        scan=scan,
    )
    db.add(membership)
    catalog = prepare_edl_review_document(
        media_item, catalog_mcf_document, root._review_synchronization(review.synchronization)
    )
    review.mcf_document = root._review_mcf_document_row(review.id, catalog)
    review_inputs = [
        ReviewFilterInput(
            id=str(uuid.uuid4()),
            source_scan_id=scan.id,
            source_finding_id=finding.id,
            source=item.source,
            category=item.category,
            subcategory=item.subcategory,
            severity=item.severity,
            mcf_channel=item.mcf_channel,
            mcf_comment=item.mcf_comment,
            label=item.label,
            details=item.details,
            group_key=item.group_key,
            start_seconds=item.start_seconds,
            end_seconds=item.end_seconds,
            confidence=item.confidence,
            edit_action=item.edit_action,
            selected=item.selected,
        )
        for item, finding in zip(parsed.filters, finding_rows)
    ]
    review_inputs = root.fill_filter_defaults(
        review_inputs, base_filter_settings(db, review.owner_id)
    ).filters
    for position, item in enumerate(review_inputs):
        db.add(
            ReviewFilterRow(
                review_id=review.id,
                id=item.id,
                source_scan_id=scan.id,
                source_finding_id=item.source_finding_id,
                position=position,
                source=item.source,
                category=item.category,
                subcategory=item.subcategory,
                severity=item.severity,
                mcf_channel=item.mcf_channel,
                mcf_comment=item.mcf_comment,
                label=item.label,
                details=item.details,
                group_key=item.group_key,
                start_seconds=item.start_seconds,
                end_seconds=item.end_seconds,
                confidence=item.confidence,
                edit_action=item.edit_action.value,
                selected=item.selected,
            )
        )
    with rollback_on_import_error(db):
        db.flush()
        bind_review_evidence(db, review)
        prepare_review_policy(db, review)
        db.commit()
    refresh_imported_rows(db, scan, review)
    return EdlImportResult(
        review=root._review_detail(db, review),
        scan=_scan_from_row(scan, finding_count=len(finding_rows)),
        warning=warning,
    )
