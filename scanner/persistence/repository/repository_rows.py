"""Convert persisted scan rows to API models."""

import json
from datetime import timezone

from sqlalchemy.orm import defer, object_session, selectinload

from scanner.db import CommunityImportRow, ScanFindingRow, ScanQueueRow, ScanRow
from scanner.modeling.model_scans import ScanVerificationCoverage
from scanner.models import (
    CreditsDetection,
    EditAction,
    Scan,
    ScanFinding,
    ScanMcfDocument,
    ScanOrigin,
    ScanQueueItem,
    ScanStageCoverage,
)

from .scan_timing import as_utc as _utc
from .scan_timing import scan_timing


def _community_provenance(row: ScanRow) -> dict | None:
    """Decode the community import provenance recorded for a scan, if any."""
    imported = row.community_import
    return (
        json.loads(imported.provenance_json)
        if imported is not None and imported.provenance_json
        else None
    )


def scan_list_loading():
    """Eager-load options for every relationship `_scan_from_row` reads.

    Community import blobs stay deferred; pair with `scan_target_approvals_by_scan`.
    """
    imported = selectinload(ScanRow.community_import).options(
        defer(CommunityImportRow.artifact_bytes),
        defer(CommunityImportRow.manifest_bytes),
        defer(CommunityImportRow.signature_bytes),
    )
    return (
        selectinload(ScanRow.media_item),
        selectinload(ScanRow.media_version_pin),
        selectinload(ScanRow.stage_coverage),
        selectinload(ScanRow.mcf_document),
        selectinload(ScanRow.credits_detection),
        imported,
    )


def _scan_from_row(
    row: ScanRow,
    *,
    finding_count: int = 0,
    community_approvals: list | None = None,
    community_pending: list | None = None,
    verification_coverage: dict[str, ScanVerificationCoverage] | None = None,
) -> Scan:
    """Convert a scan database row and supplied finding count to the API model.

    List reads can supply batch-loaded community approvals and pending targets.
    """
    from .community_import_targets import scan_pending_targets, scan_target_approvals
    from .scan_verification_coverage import verification_coverage_by_scan
    from .version_reuse import approved_reuse, reuse_state

    item = row.media_item
    duration, as_of = scan_timing(row)
    import_state = reuse_state(row)
    reuse_approval = approved_reuse(row)
    db = object_session(row) if reuse_approval and verification_coverage is None else None
    if db is not None:
        verification_coverage = verification_coverage_by_scan(db, [row])
    return Scan(
        id=row.id,
        scan_id=row.scan_number,
        name=row.name,
        origin=row.origin,
        import_source_name=row.import_source_name,
        videoskip_source=import_state.get("videoskip_source"),
        reuse_pending=import_state.get("identity_reuse_pending") is True,
        stable_version_id=row.media_version_pin.version_id if row.media_version_pin else None,
        verified_from_scan_id=reuse_approval.get("source_scan_id") if reuse_approval else None,
        verification_coverage=verification_coverage.get(row.id) if verification_coverage else None,
        imported_at=(
            row.imported_at.replace(tzinfo=timezone.utc)
            if row.imported_at is not None and row.imported_at.tzinfo is None
            else row.imported_at
        ),
        scan_mode=row.scan_mode,
        release_version=row.release_version,
        scanner_behavior_version=row.scanner_behavior_version,
        build_revision=row.build_revision,
        audio_model_revision=row.audio_model_revision,
        video_model_revision=row.video_model_revision,
        stage_coverage=[
            ScanStageCoverage(
                stage=coverage.stage,
                execution_id=coverage.execution_id,
                status=coverage.status,
                source=coverage.source,
                release_version=coverage.release_version,
                scanner_behavior_version=coverage.scanner_behavior_version,
                build_revision=coverage.build_revision,
                model_id=coverage.model_id,
                model_revision=coverage.model_revision,
                model_digest=coverage.model_digest,
                quantization=coverage.quantization,
                effective_settings=(
                    json.loads(coverage.effective_settings_json)
                    if coverage.effective_settings_json is not None
                    else None
                ),
                intervals=(
                    json.loads(coverage.intervals_json)
                    if coverage.intervals_json is not None
                    else None
                ),
                sampling_seconds=coverage.sampling_seconds,
            )
            for coverage in sorted(
                row.stage_coverage, key=lambda coverage: (coverage.stage, coverage.execution_id)
            )
        ],
        media_item_id=row.media_item_id,
        jellyfin_item_id=item.jellyfin_id,
        library_id=item.library_id,
        item_name=item.name,
        item_type=item.item_type,
        media_source_id=row.media_source_id,
        media_source_name=row.media_source_name,
        status=row.status,
        scan_audio=row.scan_audio,
        audio_model=row.audio_model,
        audio_filter=row.audio_filter,
        requested_audio_selection=(
            json.loads(row.requested_audio_selection_json)
            if row.requested_audio_selection_json
            else None
        ),
        analyzed_audio_selection=(
            json.loads(row.analyzed_audio_selection_json)
            if row.analyzed_audio_selection_json
            else None
        ),
        scan_video=row.scan_video,
        video_model=row.video_model,
        coarse_scan_interval=row.frame_interval,
        coarse_scan_enabled=row.speed_up,
        qwen_model=row.qwen_model,
        qwen_summaries_per_segment=row.qwen_summaries_per_segment,
        qwen_segment_join_gap_seconds=row.qwen_segment_join_gap_seconds,
        duration_seconds=row.duration_seconds,
        video_hash=row.video_hash,
        checkpoint_stage=row.checkpoint_stage,
        error_message=row.error_message,
        finding_count=finding_count,
        manual=row.origin == ScanOrigin.MANUAL.value,
        edited=row.selection_revision > 0,
        scan_started_at=row.worker_started_at,
        scan_duration_seconds=duration,
        scan_duration_as_of=as_of,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
        mcf_document=(
            ScanMcfDocument.model_validate(row.mcf_document, from_attributes=True)
            if row.mcf_document is not None
            else None
        ),
        credits_detection=(
            CreditsDetection(
                configuration=json.loads(row.credits_detection.configuration_json),
                detector_version=row.credits_detection.detector_version,
                cache_key=row.credits_detection.cache_key,
                result=(
                    json.loads(row.credits_detection.result_json)
                    if row.credits_detection.result_json is not None
                    else None
                ),
                completed_at=(
                    _utc(row.credits_detection.completed_at)
                    if row.credits_detection.completed_at is not None
                    else None
                ),
            )
            if row.credits_detection is not None
            else None
        ),
        community_provenance=_community_provenance(row),
        community_approvals=(
            scan_target_approvals(row) if community_approvals is None else community_approvals
        ),
        community_pending_targets=(
            scan_pending_targets(row) if community_pending is None else community_pending
        ),
    )


def _scan_finding_from_row(
    row: ScanFindingRow, *, duration_seconds: float | None = None
) -> ScanFinding:
    """Convert a finding row to the public model for its source scan."""
    end_seconds = row.end_seconds
    if duration_seconds is not None and end_seconds is not None:
        end_seconds = min(end_seconds, duration_seconds)
    return ScanFinding(
        id=row.id,
        scan_id=row.scan_id,
        category=row.category,
        subcategory=row.subcategory,
        severity=row.severity,
        mcf_channel=row.mcf_channel,
        mcf_comment=row.mcf_comment,
        start_seconds=row.start_seconds,
        end_seconds=end_seconds,
        confidence=row.confidence,
        label=row.label,
        details=row.details,
        group_key=row.group_key,
        source=row.source,
        selected=row.selected,
        edit_action=EditAction(row.edit_action),
    )


def _scan_queue_from_row(row: ScanQueueRow) -> ScanQueueItem:
    return ScanQueueItem(
        **_scan_from_row(row.scan).model_dump(),
        queue_id=row.id,
        sort_order=row.sort_order,
        progress_percent=row.scan.progress_percent,
        progress_stage=row.scan.progress_stage,
        progress_message=row.scan.progress_message,
    )
