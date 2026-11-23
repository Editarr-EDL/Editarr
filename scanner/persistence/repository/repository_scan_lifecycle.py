"""Scan creation, credits acceptance, and deletion repository operations."""

import json
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import (
    CreditsDetectionRunRow,
    MediaItemRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanMediaVersionRow,
    ScanQueueRow,
    ScanRow,
)
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.models import (
    AcceptedCreditsReference,
    CreditsAcceptanceResult,
    CreditsDetectionConfiguration,
    CreditsDetectionResult,
    EditAction,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    ReviewFilterInput,
    ReviewScanMembership,
    Scan,
    ScanMode,
    ScanOrigin,
    normalize_scan_name,
)
from scanner.pipeline.audio_tracks import probe_audio_tracks
from scanner.repository import (
    CreditsAcceptanceError,
    ReviewMembershipError,
)
from scanner.settings.filter_defaults import enforce_filter_policy

from .activity_ownership import ActivityOwner, new_review_owner_columns, new_scan_initiator_columns
from .repository_catalog import _media_format_from_path
from .review_policy import (
    owner_policy,
    policy_write,
    prepare_review_policy,
    require_policy_revision,
)
from .scan_deletion import delete_scan as delete_scan


def create_scan(
    db: Session,
    *,
    media_item: MediaItemRow,
    resolved_local_path: str,
    name: str | None = None,
    origin: ScanOrigin = ScanOrigin.GENERATED,
    source_path: str | None = None,
    media_source_id: str | None = None,
    media_source_name: str | None = None,
    scene_name: str | None = None,
    release_group: str | None = None,
    media_format: str | None = None,
    media_provider_id: str | None = None,
    duration_seconds: float | None = None,
    scan_audio: bool,
    audio_model: str | None,
    audio_filter: str | None,
    scan_video: bool,
    video_model: str | None,
    coarse_scan_interval: float | None = None,
    coarse_scan_enabled: bool = False,
    qwen_model: str = "qwen3-vl-8b-instruct-4bit",
    qwen_summaries_per_segment: int = 4,
    qwen_segment_join_gap_seconds: float = 3.0,
    frame_interval: float | None = None,
    speed_up: bool | None = None,
    scan_mode: ScanMode = ScanMode.CONTENT,
    credits_detection_configuration: CreditsDetectionConfiguration | None = None,
    initial_status: str = "queued",
    commit: bool = True,
    audio_selection: AudioSelection | dict | None = None,
    initiator: ActivityOwner | None = None,
    known_video_hash: str | None = None,
    defer_video_hash: bool = False,
) -> Scan:
    """Create a scan for the supplied media version and scanning configuration.

    `initiator` is the acting account and the permission revision it acted under.
    Every route passes it; fixtures without a principal fall back to the owner.
    """
    if (scan_mode == ScanMode.CREDITS_DETECTION) != (credits_detection_configuration is not None):
        raise ValueError("Credits detection configuration must match the scan mode")
    if scan_mode == ScanMode.CREDITS_DETECTION and (scan_audio or not scan_video):
        raise ValueError("Credits detection scans must be video-only")
    if audio_selection is not None:
        audio_selection = AudioSelection.model_validate(audio_selection)
        if not scan_audio:
            raise ValueError("Audio selection requires audio scanning")
        probe_audio_tracks(resolved_local_path).revalidate(audio_selection.selection())
    name = normalize_scan_name(name)
    video_hash = known_video_hash
    if video_hash is None and not defer_video_hash:
        video_hash = root._compute_video_hash(resolved_local_path)
    try:
        video_duration_seconds = root.get_video_duration(resolved_local_path)
    except (OSError, RuntimeError, ValueError):
        video_duration_seconds = duration_seconds
    if coarse_scan_interval is None:
        coarse_scan_interval = frame_interval
    if speed_up is not None:
        coarse_scan_enabled = speed_up
    last = (
        db.query(ScanRow.scan_number)
        .filter(ScanRow.media_item_id == media_item.id)
        .order_by(ScanRow.scan_number.desc())
        .first()
    )
    row = ScanRow(
        id=str(uuid.uuid4()),
        media_item_id=media_item.id,
        scan_number=(last[0] if last else 0) + 1,
        name=name,
        origin=origin.value,
        scan_mode=scan_mode,
        source_path=source_path or media_item.path or resolved_local_path,
        media_source_id=media_source_id,
        media_source_name=media_source_name,
        resolved_local_path=resolved_local_path,
        video_hash=video_hash,
        scene_name=scene_name,
        release_group=release_group,
        media_format=media_format or _media_format_from_path(resolved_local_path),
        media_provider_id=media_provider_id,
        duration_seconds=video_duration_seconds,
        status=initial_status,
        scan_audio=scan_audio,
        audio_model=audio_model,
        audio_filter=audio_filter,
        requested_audio_selection_json=(
            audio_selection.model_dump_json() if audio_selection else None
        ),
        scan_video=scan_video,
        video_model=video_model,
        frame_interval=coarse_scan_interval,
        speed_up=coarse_scan_enabled,
        qwen_model=qwen_model,
        qwen_summaries_per_segment=qwen_summaries_per_segment,
        qwen_segment_join_gap_seconds=qwen_segment_join_gap_seconds,
        progress_percent=0.0,
        progress_stage=initial_status,
        progress_message=(
            "Waiting for model consent" if initial_status == "awaiting_model_consent" else "Queued"
        ),
        **(
            {
                "initiator_id": initiator.user_id,
                "initiator_permission_revision": initiator.permission_revision,
            }
            if initiator is not None
            else new_scan_initiator_columns(db)
        ),
    )
    db.add(row)
    if credits_detection_configuration is not None:
        row.credits_detection = CreditsDetectionRunRow(
            configuration_json=json.dumps(
                credits_detection_configuration.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ),
            detector_version=credits_detection_configuration.detector_version,
        )
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return root._scan_from_row(row)


def _validated_credits_acceptance(
    db: Session,
    *,
    scan_id: str,
    accepted_boundary_seconds: float,
    media_item_id: str,
    media_source_id: str,
    resolved_local_path: str | None = None,
):
    """Resolve and validate the candidate finding for an accepted boundary."""
    row = db.get(ScanRow, scan_id)
    readiness_failure = root._credits_readiness_failure(row)
    if readiness_failure is not None:
        code, message, status_code = readiness_failure
        raise CreditsAcceptanceError(code, message, status_code)
    assert row is not None and row.credits_detection is not None
    if not root._scan_is_available(
        db,
        row,
        media_item_id=media_item_id,
        media_source_id=media_source_id,
    ):
        raise CreditsAcceptanceError(
            "credits_target_mismatch",
            "Credits detection does not match the media version",
            409,
        )
    if resolved_local_path is not None and not root._scan_matches_physical_version(
        row, Path(resolved_local_path), require_hash=True
    ):
        raise CreditsAcceptanceError(
            "credits_target_mismatch",
            "Credits detection does not match the physical media file",
            409,
        )
    if row.duration_seconds is None:
        raise CreditsAcceptanceError(
            "media_runtime_unavailable", "Media runtime is unavailable", 409
        )
    if accepted_boundary_seconds >= row.duration_seconds:
        raise CreditsAcceptanceError(
            "invalid_credits_boundary",
            "Accepted credits boundary must be before file end",
            422,
        )
    result = CreditsDetectionResult.model_validate_json(row.credits_detection.result_json)
    if result.outcome != "candidate" or result.best_candidate_id is None:
        raise CreditsAcceptanceError(
            "credits_detection_not_ready",
            "Credits detection has no candidate to accept",
            409,
        )
    candidate = next(
        (value for value in result.candidates if value.id == result.best_candidate_id),
        None,
    )
    if candidate is None:
        raise CreditsAcceptanceError(
            "credits_candidate_not_found",
            "Credits detection candidate was not found",
            404,
        )
    finding = (
        db.query(ScanFindingRow)
        .filter(
            ScanFindingRow.scan_id == scan_id,
            ScanFindingRow.start_seconds == candidate.boundary_seconds,
            ScanFindingRow.category == McfCategoryFamily.DISPENSABLE_SCENES,
            ScanFindingRow.subcategory == McfCategory.dispensable,
        )
        .one_or_none()
    )
    if finding is None:
        raise CreditsAcceptanceError(
            "credits_candidate_not_found",
            "Credits detection candidate was not found",
            404,
        )
    return row, candidate, finding


@policy_write
def accept_credits_detection(
    db: Session,
    *,
    scan_id: str,
    accepted_boundary_seconds: float,
    media_item_id: str,
    media_source_id: str | None,
    resolved_local_path: str,
    review_id: str | None = None,
    revision: int | None = None,
    policy_owner_id: int | None = None,
    policy_revision: int | None = None,
) -> CreditsAcceptanceResult:
    """Build a review membership and filter from a validated credits candidate."""
    normalized_media_source_id = media_source_id or ""
    if review_id is not None:
        review = db.get(SavedReviewRow, review_id)
        if review is None:
            raise ReviewMembershipError("Saved review was not found")
        if revision is None:
            raise ReviewMembershipError("Saved review revision is required")
        root._check_review_revision(db, review, revision)
        if (
            review.media_item_id != media_item_id
            or review.media_source_id != normalized_media_source_id
        ):
            raise ReviewMembershipError("Credits detection does not match the saved review")
    scan, candidate, finding = _validated_credits_acceptance(
        db,
        scan_id=scan_id,
        accepted_boundary_seconds=accepted_boundary_seconds,
        media_item_id=media_item_id,
        media_source_id=normalized_media_source_id,
        resolved_local_path=resolved_local_path,
    )
    accepted = AcceptedCreditsReference(
        scan_id=scan.id,
        candidate_id=candidate.id,
        accepted_boundary_seconds=accepted_boundary_seconds,
    )
    base_filter = ReviewFilterInput(
        id=str(uuid.uuid4()),
        source_scan_id=scan.id,
        source_finding_id=finding.id,
        source="visual",
        category=McfCategoryFamily.DISPENSABLE_SCENES,
        subcategory=McfCategory.dispensable,
        severity=finding.severity,
        mcf_channel=McfChannel.VIDEO,
        mcf_comment="End Credits",
        label="End Credits",
        details="End Credits",
        group_key=finding.group_key,
        start_seconds=accepted_boundary_seconds,
        end_seconds=scan.duration_seconds,
        confidence=candidate.confidence,
        edit_action=EditAction.SKIP,
        selected=True,
    )
    if review_id is not None:
        prepared = prepare_review_policy(db, review, filters=[base_filter])
        require_policy_revision(prepared, policy_revision, policy_owner_id)
        effective = ReviewFilterInput.model_validate(prepared.filters[0].model_dump())
        owner_id = prepared.owner_id
        used_revision = prepared.policy_revision
    else:
        owner_id = new_review_owner_columns(db)["owner_id"]
        policy = owner_policy(db, owner_id)
        effective = enforce_filter_policy([base_filter], policy).filters[0]
        used_revision = policy.policy_revision
    return CreditsAcceptanceResult(
        accepted_credits=accepted,
        membership=ReviewScanMembership(scan_id=scan.id, role="video"),
        filter=effective,
        base_filter=base_filter,
        policy_owner_id=owner_id,
        policy_revision=used_revision,
    )


def get_credits_detection(db: Session, scan_id: str) -> Scan | None:
    row = db.get(ScanRow, scan_id)
    if row is None or row.scan_mode != ScanMode.CREDITS_DETECTION:
        return None
    root._validated_credits_media_path(db, row)
    finding_count = db.query(ScanFindingRow).filter(ScanFindingRow.scan_id == scan_id).count()
    return root._scan_from_row(row, finding_count=finding_count)


def find_active_credits_detection(
    db: Session,
    *,
    media_item_id: str,
    resolved_local_path: str,
    media_source_id: str | None,
    configuration: CreditsDetectionConfiguration,
    initiator_id: int,
    version_id: str | None = None,
    generation: tuple[int, ...] | None = None,
    legacy_generation_proven: bool = False,
) -> tuple[Scan, int] | None:
    """Find this initiator's matching queued or running credits scan for a media version."""
    configuration_json = json.dumps(
        configuration.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    matching = (
        db.query(ScanQueueRow)
        .join(ScanRow)
        .join(CreditsDetectionRunRow)
        .filter(
            ScanRow.scan_mode == ScanMode.CREDITS_DETECTION,
            ScanRow.media_item_id == media_item_id,
            ScanRow.resolved_local_path == resolved_local_path,
            ScanRow.media_source_id == media_source_id,
            ScanRow.initiator_id == initiator_id,
            ScanRow.status.in_(
                (
                    "awaiting_model_consent",
                    "acquiring_model",
                    "queued",
                    "scanning",
                    "cancelling",
                    "orphaned",
                )
            ),
            CreditsDetectionRunRow.configuration_json == configuration_json,
        )
    )
    if version_id is not None:
        matching = matching.join(ScanMediaVersionRow, ScanMediaVersionRow.scan_id == ScanRow.id)
        matching = matching.filter(ScanMediaVersionRow.version_id == version_id)
    matching = matching.order_by(ScanQueueRow.id)
    if generation is None:
        row = matching.first()
    else:
        from scanner.runtime.file_generation import generation_matches
        from scanner.runtime.scan_version import recorded_scan_generation

        row = next(
            (
                candidate
                for candidate in matching
                if generation_matches(stamp := recorded_scan_generation(candidate.scan), generation)
                or (stamp is None and legacy_generation_proven)
            ),
            None,
        )
    if row is None:
        return None
    if version_id is None and not root._scan_matches_physical_version(
        row.scan, Path(resolved_local_path), require_hash=True
    ):
        return None
    return root._scan_from_row(row.scan), row.id
