"""Preview and explicitly approve verified scan reuse on a stable media version."""

from dataclasses import dataclass
from datetime import UTC, datetime
from hmac import compare_digest
from math import isfinite
from pathlib import Path

from fastapi import APIRouter, Depends

from scanner import api as root
from scanner.db import MediaItemRow, ScanRow
from scanner.modeling.model_version_reuse import (
    ReuseAnchor,
    VersionReuseApproval,
    VersionReusePreview,
    VersionReuseRequest,
    VersionReuseResult,
    VersionReuseTranscriptStatus,
)
from scanner.modeling.version_alignment import Alignment
from scanner.persistence.repository import media_identity_store as identity
from scanner.persistence.repository import version_reuse as reuse
from scanner.persistence.repository.repository_catalog import get_scan
from scanner.persistence.repository.review_access import ReviewAccess, not_found
from scanner.persistence.repository.scan_jobs import scan_job_facts
from scanner.runtime.fingerprint_activity import (
    FingerprintActivity,
    active_fingerprints,
    track_fingerprint,
)
from scanner.runtime.scan_version import (
    ScanVersionChanged,
    file_generation,
    generation_token,
)
from scanner.runtime.version_reuse_audio import analyzed_selection, select_target_audio
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.targets import AccountRef, ResourceRef

from .reuse_alignment import align_from_transcript, alignment_transcript_status
from .review_access import review_caller
from .review_targets import ReviewTarget, capture_target
from .scan_access import ScanCaller
from .stable_media_capture import cached_target_digest, file_digest, observe_target_version

router = APIRouter()


def _fingerprint_actor(caller: ReviewAccess) -> tuple[int, int]:
    principal = caller.access.principal
    return int(principal.user_id), int(principal.permission_revision)


@router.get("/api/media-fingerprints/queue", response_model=list[FingerprintActivity])
def fingerprint_queue(caller: ReviewAccess = Depends(review_caller)):
    """Report the caller's active fingerprint work."""
    return active_fingerprints(_fingerprint_actor(caller))


CURRENT_VERSION = "current"


@dataclass(frozen=True)
class UnpinnedTarget:
    title_id: str
    content_sha256: None = None


def _file_stamp(path: Path) -> tuple[int, ...]:
    try:
        return file_generation(str(path))
    except ScanVersionChanged as exc:
        raise root._review_http_error(
            "target_changed", "The selected version is unavailable", 409
        ) from exc


def _source(caller, scan_id, target_version):
    facts = scan_job_facts(caller.db, [scan_id]).get(scan_id)
    if facts is None or facts.status != "complete" or facts.mode != "content":
        raise not_found()
    caller.require_scan(facts, caller.media_decision(facts.media))
    source_version = identity.get_version(caller.db, identity.scan_version_id(caller.db, scan_id))
    if source_version is None or source_version.title_id != target_version.title_id:
        raise not_found()
    return caller.db.get(ScanRow, scan_id), source_version


def _target(caller, request, version_id):
    target = capture_target(caller, request.target)
    if target.source.media_source_id != request.target.media_source_id:
        raise not_found()
    principal = caller.access.principal
    media = caller.media_decision(target.media)
    resource = ResourceRef(AccountRef(principal.user_id, principal.account.role), media)
    require(caller.access, C.REVIEWS_CREATE_OWN, resource)
    require(caller.access, C.REVIEWS_IMPORT_UPLOAD, resource)
    title_id = identity.find_title_id(
        caller.db, server_id=target.source.server_id, jellyfin_id=target.source.item_id
    )
    if not title_id:
        raise not_found()
    item = target.source.catalog_item
    if item and identity.identity_conflicts_title(caller.db, title_id, item.media_identity):
        raise root._review_http_error(
            "target_changed", "Jellyfin's provider IDs conflict with this Editarr title", 409
        )
    if version_id == CURRENT_VERSION:
        return target, UnpinnedTarget(title_id)
    version = identity.get_version(caller.db, version_id)
    if version is None or not version.content_sha256 or title_id != version.title_id:
        raise not_found()
    return target, version


def _target_runtime(target: ReviewTarget) -> float:
    try:
        seconds = root.get_video_duration(target.media.resolved_path)
    except (OSError, RuntimeError, ValueError):
        seconds = target.source.duration_seconds
    if seconds is None or not isfinite(seconds) or not 0 < seconds <= 172800:
        raise root._review_http_error(
            "media_runtime_unavailable", "Target playback duration is unavailable", 409
        )
    return seconds


def _identity_anchors(source_start, source_end):
    if source_end <= source_start:
        return []
    return [
        ReuseAnchor(source_seconds=point, target_seconds=point)
        for point in (source_start, source_end)
    ]


def _alignment(
    caller, request, source, source_version, target, version, path, duration, audio, digest
):
    include_audio = "audio" in request.channels
    same_content = (
        source.origin == "generated"
        and version.content_sha256 is not None
        and source_version.content_sha256 == version.content_sha256
    )
    original_audio = analyzed_selection(source) if include_audio else None
    same_track = not include_audio or (
        original_audio is not None and original_audio.track == audio.track
    )
    if same_content and same_track:
        return Alignment.from_anchors(_identity_anchors(0, duration)), True, [], None
    if request.method == "transcript":
        alignment, issues, evidence_id = align_from_transcript(
            caller, source, source_version, target, path, audio, duration, digest
        )
        return alignment, False, issues, evidence_id
    if request.method == "original":
        document = source.mcf_document
        start = document.material_start_seconds if document else 0
        end = document.material_end_seconds if document else source.duration_seconds
        limit = min(duration, end) if end is not None else duration
        return (
            Alignment.from_anchors(_identity_anchors(start, limit)),
            False,
            [
                "Check cue times on the target before approving the original timestamps",
            ],
            None,
        )
    if not request.anchors:
        return (
            Alignment.from_anchors([]),
            False,
            ["Add matched timing points and preview the cues on this version"],
            None,
        )
    if any(anchor.target_seconds > duration for anchor in request.anchors):
        raise ValueError("Target timing points must be within the target playback duration")
    if source.origin == "generated" and source.duration_seconds is not None:
        if any(anchor.source_seconds > source.duration_seconds for anchor in request.anchors):
            raise ValueError("Original timing points must be within the original playback duration")
    return Alignment.from_anchors(request.anchors), False, [], None


@dataclass(frozen=True)
class CapturedReuse:
    target: ReviewTarget
    source_version_id: str
    path: Path
    stamp: tuple
    digest: str | None
    duration: float
    source_stamp: str
    audio: object
    preview: VersionReusePreview


def _capture(caller, version_id, request, *, verify_existing=False) -> CapturedReuse:
    target, version = _target(caller, request, version_id)
    source, source_version = _source(caller, request.source_scan_id, version)
    path = Path(target.media.resolved_path)
    before = _file_stamp(path)
    audio = (
        select_target_audio(path, request.audio_track_index)
        if "audio" in request.channels or request.method == "transcript"
        else None
    )
    digest = cached_target_digest(
        caller.db,
        target,
        audio_evidence=(before, audio.file_version) if audio else None,
    )
    check_transcript = (
        verify_existing
        and digest is None
        and request.method == "transcript"
        and alignment_transcript_status(caller, source, target, audio, None)[1]
    )
    if digest is None and (check_transcript or version.content_sha256):
        digest = _tracked_digest(caller, target, path, before, request.source_scan_id)
    if version.content_sha256 and digest != version.content_sha256:
        raise root._review_http_error("target_changed", "The selected version has changed", 409)
    digest = digest or version.content_sha256
    duration = _target_runtime(target)
    findings = reuse.source_findings(caller.db, source.id)
    signature = reuse.source_signature(source, findings)
    alignment, exact, issues, alignment_scan = _alignment(
        caller,
        request,
        source,
        source_version,
        target,
        version,
        path,
        duration,
        audio,
        digest,
    )
    anchors = alignment.anchors
    cues = reuse.project_cues(findings, request.channels, alignment, duration)
    mapped = {cue.channel for cue in cues if cue.target_start is not None}
    channels = [name for name in request.channels if name in mapped or "both" in mapped]
    excluded = sum(cue.target_start is None for cue in cues)
    if excluded:
        issues.append(f"No reliable target timing for {excluded} cue(s). Check the original scan.")
    if not cues:
        issues.append("The selected channels have no source cues to copy")
    if _file_stamp(path) != before:
        raise root._review_http_error("target_changed", "The selected version has changed", 409)
    principal = caller.access.principal
    token = (
        reuse.approval_token(
            request,
            version_id=version_id,
            source_version_id=source_version.id,
            source_stamp=signature,
            actor_stamp=(int(principal.user_id), int(principal.permission_revision)),
            target_stamp=(
                *before,
                version.content_sha256,
                audio.extraction_cache_key() if audio else None,
            ),
            duration=duration,
            anchors=anchors,
            cues=cues,
            alignment_audio_scan_id=alignment_scan,
        )
        if channels
        else None
    )
    return CapturedReuse(
        target,
        source_version.id,
        path,
        before,
        digest,
        duration,
        signature,
        audio,
        VersionReusePreview(
            source_scan_id=source.id,
            version_id=version_id,
            token=token,
            target_generation=generation_token(before),
            verified_channels=channels,
            exact=exact,
            issues=issues,
            cues=cues,
            anchors=anchors,
            alignment_audio_scan_id=alignment_scan,
        ),
    )


def _capture_request(caller, version_id, request, *, verify_existing=False) -> CapturedReuse:
    try:
        return _capture(caller, version_id, request, verify_existing=verify_existing)
    except ValueError as exc:
        raise root._review_http_error("invalid_reuse_alignment", str(exc), 422) from exc


@router.post(
    "/api/media-versions/{version_id}/reuse/transcript-status",
    response_model=VersionReuseTranscriptStatus,
    response_model_exclude_defaults=True,
)
def transcript_status(
    version_id: str,
    request: VersionReuseRequest,
    caller: ReviewAccess = Depends(review_caller),
):
    """Report transcript readiness for an authorized source and target track."""
    target, version = _target(caller, request, version_id)
    source, _ = _source(caller, request.source_scan_id, version)
    path = Path(target.media.resolved_path)
    stamp = _file_stamp(path)
    try:
        audio = select_target_audio(path, request.audio_track_index)
        digest = cached_target_digest(caller.db, target, audio_evidence=(stamp, audio.file_version))
        if digest and version.content_sha256 and digest != version.content_sha256:
            raise root._review_http_error("target_changed", "The selected version has changed", 409)
        ready, verify_existing = alignment_transcript_status(caller, source, target, audio, digest)
    except ValueError as exc:
        raise root._review_http_error("invalid_reuse_alignment", str(exc), 422) from exc
    if _file_stamp(path) != stamp:
        raise root._review_http_error("target_changed", "The selected version has changed", 409)
    return VersionReuseTranscriptStatus(ready=ready, verify_existing=verify_existing)


@router.post("/api/media-versions/{version_id}/reuse/preview", response_model=VersionReusePreview)
def preview_version_reuse(
    version_id: str,
    request: VersionReuseRequest,
    caller: ReviewAccess = Depends(review_caller),
):
    """Prepare an actor/file-bound preview without changing their reviews or scans."""
    return _capture_request(caller, version_id, request, verify_existing=True).preview


def _tracked_digest(caller, target, path, stamp, source_scan_id):
    source = target.source
    with track_fingerprint(
        actor=_fingerprint_actor(caller),
        item_name=source.name,
        version_name=source.media_source_name,
        generation=stamp,
        item_id=source.item_id,
        media_source_id=source.media_source_id,
        source_scan_id=source_scan_id,
    ) as report:
        return file_digest(str(path), on_progress=report)


@router.post("/api/media-versions/{version_id}/reuse/approve", response_model=VersionReuseResult)
def approve_version_reuse(
    version_id: str,
    request: VersionReuseApproval,
    caller: ReviewAccess = Depends(review_caller),
):
    """Attach only the confirmed mapped cues to an authorized exact target."""
    captured = _capture_request(caller, version_id, request)
    if captured.preview.token is None or not compare_digest(request.token, captured.preview.token):
        raise root._review_http_error(
            "reuse_preview_changed", "Preview the current alignment again", 409
        )

    # Read the entire file only after validating the actor, preview, and confirmation.
    digest = captured.digest
    if digest is None:
        digest = _tracked_digest(
            caller, captured.target, captured.path, captured.stamp, request.source_scan_id
        )
    if _file_stamp(captured.path) != captured.stamp:
        raise root._review_http_error("target_changed", "The selected version has changed", 409)

    def persist(db):
        current = db.get(ScanRow, request.source_scan_id)
        findings = reuse.source_findings(db, request.source_scan_id)
        if (
            current is None
            or reuse.source_signature(current, findings) != captured.source_stamp
            or identity.scan_version_id(db, request.source_scan_id) != captured.source_version_id
            or _file_stamp(captured.path) != captured.stamp
        ):
            raise root._review_http_error(
                "reuse_preview_changed",
                "Preview the current alignment again",
                409,
            )
        observed_id = observe_target_version(db, captured.target, digest=digest)
        if version_id != CURRENT_VERSION and observed_id != version_id:
            raise root._review_http_error("target_changed", "The selected version has changed", 409)
        media = (
            db.query(MediaItemRow)
            .filter_by(
                server_id=captured.target.source.server_id,
                jellyfin_id=captured.target.source.item_id,
            )
            .one()
        )
        scan, reused = reuse.stage_verified_reuse(
            db,
            media=media,
            target=captured.target,
            version_id=observed_id,
            source_version_id=captured.source_version_id,
            source=current,
            findings=findings,
            preview=captured.preview,
            request=request,
            audio_selection=captured.audio,
            duration=captured.duration,
            target_sha256=digest,
        )
        if _file_stamp(captured.path) != captured.stamp:
            raise root._review_http_error("target_changed", "The selected version has changed", 409)
        db.commit()
        result = get_scan(db, scan.id)
        return VersionReuseResult(
            scan=ScanCaller(caller.access, db, datetime.now(UTC)).present(result),
            reused=reused,
        )

    source_ids = {request.source_scan_id}
    if captured.preview.alignment_audio_scan_id:
        source_ids.add(captured.preview.alignment_audio_scan_id)
    return caller.create(
        captured.target.media,
        persist,
        scan_ids=source_ids,
        capability=C.REVIEWS_IMPORT_UPLOAD,
        capabilities=(
            C.REVIEWS_EVIDENCE_EDIT_OWN,
            C.REVIEWS_CUES_EDIT_OWN,
            C.REVIEWS_TIMING_EDIT_OWN,
        ),
    )
