"""Credits detection and acceptance routes."""

import math
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from scanner import api as root
from scanner.api import _review_error_responses
from scanner.credits.credits_detector import default_configuration
from scanner.db import MediaItemRow, SavedReviewRow
from scanner.models import (
    CreditsAcceptanceRequest,
    CreditsAcceptanceResult,
    CreditsDetectionStartRequest,
    CreditsDetectionStartResult,
    CreditsModelConsent,
    CreditsModelConsentRequest,
    Scan,
    ScanMode,
)
from scanner.persistence.repository.media_identity_store import (
    bind_scan_version,
    get_version,
    observe_sampled_version,
)
from scanner.persistence.repository.review_access import CREDITS, ReviewAccess, not_found
from scanner.persistence.repository.scan_jobs import ScanMedia, scan_job_facts
from scanner.repository import (
    CreditsRouteError,
    accept_credits_detection,
    create_scan,
    enqueue_scan,
    find_active_credits_detection,
    get_credits_detection,
    get_scan,
    get_scan_queue_item_by_scan_id,
    render_credits_detection_preview,
    resolve_local_path,
    update_credits_detection_configuration,
    upsert_media_item,
)
from scanner.runtime.media_fingerprints import cached_content_digest, sampled_media_fingerprint
from scanner.runtime.scan_version import ScanVersionChanged, file_generation, stage_scan_generation
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.targets import INSTANCE
from scanner.security.scan_jobs.authority import JobGate

from . import background
from .credits_acquisition import run_credits_model_acquisition as _run_credits_model_acquisition
from .review_access import review_caller, run_review
from .review_import_helpers_api import _import_target_media
from .review_targets import capture_target
from .scan_access import JOB_RESUME, ScanCaller, scan_caller
from .stable_media_capture import require_media_generation

router = APIRouter()


def _credits_target_media(db: Session, target, *, persist: bool = True):
    """Resolve a credits detection target and verify its media runtime."""
    try:
        media, media_source_id, _catalog_document = _import_target_media(
            db, target, **({"persist": False} if not persist else {})
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") != "media_unavailable":
            raise
        if exc.status_code in {400, 404}:
            raise root._review_http_error(
                "invalid_credits_target", "Credits detection target is invalid", 400
            ) from exc
        raise root._review_http_error(
            "credits_media_unavailable", "Credits media is unavailable", 503
        ) from exc
    if not persist:
        media = MediaItemRow(
            server_id=media.server_id,
            jellyfin_id=media.jellyfin_id,
            library_id=media.library_id,
            item_type=media.item_type,
            name=media.name,
            path=media.path,
        )
    if not media.path:
        raise root._review_http_error(
            "credits_media_unavailable", "Credits media is unavailable", 503
        )
    try:
        local_path = Path(resolve_local_path(db, media.path).local_path).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise root._review_http_error(
            "credits_media_unavailable", "Credits media is unavailable", 503
        ) from exc
    if not local_path.is_file() or not os.access(local_path, os.R_OK):
        raise root._review_http_error(
            "credits_media_unavailable", "Credits media is unavailable", 503
        )
    try:
        duration_seconds = root.get_video_duration(str(local_path))
    except (OSError, RuntimeError, ValueError) as exc:
        raise root._review_http_error(
            "media_runtime_unavailable", "Media runtime is unavailable", 409
        ) from exc
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise root._review_http_error(
            "media_runtime_unavailable", "Media runtime is unavailable", 409
        )
    return media, media_source_id, local_path, duration_seconds


def _credits_model_consent(qwen_model: str) -> CreditsModelConsent:
    """Describe whether the requested optional credits model needs consent."""
    capability = root.qwen_enrichment_capability()
    service = root.get_acquisition_service()
    try:
        artifact = service.status(qwen_model)
    except ValueError as exc:
        raise root._review_http_error(
            "invalid_credits_target", "Credits detection target is invalid", 400
        ) from exc
    return CreditsModelConsent(
        backend_id="qwen3_vl",
        artifact_ids=[artifact.artifact_id],
        expected_size=artifact.bytes_total or 0,
        availability=capability.availability,
        device=capability.device,
        license_spdx=artifact.license_spdx,
        license_status=artifact.license_status,
    )


def _credits_consent_if_required(qwen_model: str | None) -> CreditsModelConsent | None:
    if qwen_model is None:
        return None
    consent = _credits_model_consent(qwen_model)
    capability = root.qwen_enrichment_capability()
    artifact_state = root.get_acquisition_service().status(qwen_model).state
    if capability.availability == "available" and artifact_state == "ready":
        return None
    return consent


@router.post(
    "/api/credits-detections",
    response_model=CreditsDetectionStartResult,
    status_code=202,
    responses=_review_error_responses(400, 409, 503),
)
def api_start_credits_detection(
    request: CreditsDetectionStartRequest, caller: ScanCaller = Depends(scan_caller)
):
    """Create or reuse this caller's credits detection scan for the target media version."""
    db = caller.db
    try:
        configuration = default_configuration(qwen_model=request.qwen_model)
        media, media_source_id, local_path, duration_seconds = _credits_target_media(
            db, request.target, persist=False
        )
        consent = _credits_consent_if_required(request.qwen_model)
        resolved = str(local_path)
        target = ScanMedia(media.server_id, media.jellyfin_id, media.library_id, resolved)
        caller.require_start(C.SCANS_START, target)
        try:
            generation = file_generation(resolved)
            fingerprint = sampled_media_fingerprint(local_path)
            known_sha256 = cached_content_digest(local_path)
        except (OSError, ValueError) as exc:
            raise root._review_http_error(
                "credits_media_unavailable", "The selected media file could not be verified", 409
            ) from exc
        with caller.starting(C.SCANS_START, target):
            require_media_generation(db, media.path, local_path, generation)
            if fingerprint.generation != generation:
                raise ScanVersionChanged("The selected media file changed while preparing the scan")
            media = upsert_media_item(
                db,
                server_id=media.server_id,
                jellyfin_id=media.jellyfin_id,
                library_id=media.library_id,
                item_type=media.item_type,
                name=media.name,
                path=media.path,
                commit=False,
            )
            version_id = observe_sampled_version(
                db,
                media=media,
                media_source_id=media_source_id,
                source_path=media.path,
                fingerprint=fingerprint,
                known_content_sha256=known_sha256,
            )
            legacy_generation_proven = bool(
                known_sha256 and get_version(db, version_id).content_sha256 == known_sha256
            )
            active = find_active_credits_detection(
                db,
                media_item_id=media.id,
                resolved_local_path=resolved,
                media_source_id=media_source_id,
                configuration=configuration,
                initiator_id=caller.initiator.user_id,
                version_id=version_id,
                generation=generation,
                legacy_generation_proven=legacy_generation_proven,
            )
            if active is None:
                scan = create_scan(
                    db,
                    media_item=media,
                    resolved_local_path=resolved,
                    name="End Credits detection",
                    source_path=media.path,
                    media_source_id=media_source_id,
                    duration_seconds=duration_seconds,
                    scan_audio=False,
                    audio_model=None,
                    audio_filter=None,
                    scan_video=True,
                    video_model=None,
                    scan_mode=ScanMode.CREDITS_DETECTION,
                    credits_detection_configuration=configuration,
                    initial_status=("awaiting_model_consent" if consent is not None else "queued"),
                    commit=False,
                    initiator=caller.initiator,
                    known_video_hash=fingerprint.digest,
                )
                bind_scan_version(db, scan.id, version_id)
                stage_scan_generation(db, scan.id, resolved, generation)
                require_media_generation(db, media.path, local_path, generation)
                queue = enqueue_scan(db, scan.id)
                queue_id = queue.queue_id
            else:
                require_media_generation(db, media.path, local_path, generation)
                queue, queue_id = active
                db.commit()
    except ScanVersionChanged as exc:
        db.rollback()
        raise root._review_http_error("credits_media_unavailable", str(exc), 409) from exc
    except HTTPException:
        db.rollback()
        raise
    except AccessDenied:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        raise root._review_http_error(
            "invalid_credits_target", "Credits detection request is invalid", 400
        ) from exc
    if active is None and consent is None:
        root._start_scan_queue_worker_if_needed()
    return CreditsDetectionStartResult(
        scan=caller.present(queue),
        queue_id=queue_id,
        model_consent=(consent if queue.status == "awaiting_model_consent" else None),
    )


@router.get(
    "/api/credits-detections/{scan_id}",
    response_model=Scan,
    responses=_review_error_responses(404, 409, 503),
)
def api_get_credits_detection(scan_id: str, caller: ScanCaller = Depends(scan_caller)):
    seen = caller.seen([scan_id]).get(scan_id)
    if seen is None:
        raise root._review_http_error("scan_not_found", "Credits detection scan was not found", 404)
    try:
        scan = get_credits_detection(caller.db, scan_id)
    except CreditsRouteError as exc:
        raise root._review_http_error(exc.code, str(exc), exc.status_code) from exc
    if scan is None:
        raise root._review_http_error("scan_not_found", "Credits detection scan was not found", 404)
    return caller.present(scan, seen)


@router.post(
    "/api/credits-detections/{scan_id}/model-consent",
    response_model=CreditsDetectionStartResult,
    status_code=202,
    responses=_review_error_responses(404, 409, 503),
)
def api_credits_detection_model_consent(
    scan_id: str,
    request: CreditsModelConsentRequest,
    caller: ScanCaller = Depends(scan_caller),
):
    """Apply model consent and resume the pending credits scan.

    Consent continues a waiting job, so it needs the job's resume action, and
    acquiring the model needs `models.download`.
    """
    db = caller.db
    seen = caller.seen([scan_id]).get(scan_id)
    if seen is None or not seen.job:
        raise root._review_http_error("scan_not_found", "Credits detection scan was not found", 404)
    caller.require(JOB_RESUME, caller.ref(seen))
    caller.require(C.MODELS_DOWNLOAD, INSTANCE)
    scan = get_scan(db, scan_id)
    queue = get_scan_queue_item_by_scan_id(db, scan_id)
    if scan is None or scan.scan_mode != ScanMode.CREDITS_DETECTION:
        raise root._review_http_error("scan_not_found", "Credits detection scan was not found", 404)
    if scan.credits_detection is None or queue is None or scan.status != "awaiting_model_consent":
        raise root._review_http_error(
            "credits_detection_not_ready",
            "Credits detection is not ready for model consent",
            409,
        )
    qwen_model = scan.credits_detection.configuration.qwen_model
    if qwen_model is None:
        raise root._review_http_error(
            "credits_detection_not_ready",
            "Credits detection is not ready for model consent",
            409,
        )
    if request.decision == "continue_without_qwen":
        updated = update_credits_detection_configuration(
            db,
            scan_id,
            scan.credits_detection.configuration.model_copy(update={"qwen_model": None}),
            status="queued",
            progress_message="Queued without Qwen",
        )
        root._start_scan_queue_worker_if_needed()
        return CreditsDetectionStartResult(
            scan=caller.present(updated, seen), queue_id=queue.queue_id
        )

    consent = _credits_model_consent(qwen_model)
    if consent.availability != "available":
        raise root._review_http_error(
            "credits_model_acquisition_failed",
            "Optional model acquisition is unavailable",
            503,
        )
    updated = update_credits_detection_configuration(
        db,
        scan_id,
        scan.credits_detection.configuration,
        status="acquiring_model",
        progress_message="Acquiring optional Qwen model",
    )
    tasks = root._model_tasks()
    with root._model_task_lock():
        key = f"credits:{scan_id}"
        existing = tasks.get(key)
        if existing is None or not existing.is_alive():
            gate = JobGate(root.get_session_factory(), scan_id)
            tasks[key] = background.start_thread(
                f"editarr-credits-model-{scan_id}",
                gate,
                lambda: _run_credits_model_acquisition(gate, qwen_model),
            )
    return CreditsDetectionStartResult(scan=caller.present(updated, seen), queue_id=queue.queue_id)


@router.post(
    "/api/credits-detections/{scan_id}/accept",
    response_model=CreditsAcceptanceResult,
    responses=_review_error_responses(400, 404, 409, 422, 500, 503),
)
def api_accept_credits_detection(
    scan_id: str,
    request: CreditsAcceptanceRequest,
    caller: ReviewAccess = Depends(review_caller),
):
    """Validate an edited credits boundary and return review draft additions."""
    db = caller.db
    try:
        if request.context.kind == "review":

            def accept_review(session, review_id):
                row = session.get(SavedReviewRow, review_id)
                return accept_credits_detection(
                    session,
                    scan_id=scan_id,
                    accepted_boundary_seconds=request.accepted_boundary_seconds,
                    media_item_id=row.media_item_id,
                    media_source_id=row.media_source_id,
                    resolved_local_path=resolve_local_path(session, row.media_item.path).local_path,
                    review_id=review_id,
                    revision=request.context.revision,
                    policy_owner_id=request.context.policy_owner_id,
                    policy_revision=request.context.policy_revision,
                )

            return run_review(
                caller,
                request.context.review_id,
                CREDITS,
                accept_review,
                scan_ids=[scan_id],
                allow_jobs=True,
            )
        if C.REVIEWS_IMPORT_UPLOAD not in caller.access.capabilities:
            from scanner.security.access.api_shapes import AccessErrorCode

            raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
        prepared = capture_target(caller, request.context.target)

        def accept_import(session):
            media = prepared.store(session)
            return accept_credits_detection(
                session,
                scan_id=scan_id,
                accepted_boundary_seconds=request.accepted_boundary_seconds,
                media_item_id=media.id,
                media_source_id=prepared.source.media_source_id,
                resolved_local_path=prepared.media.resolved_path,
            )

        return caller.create(
            prepared.media,
            accept_import,
            scan_ids=[scan_id],
            capability=C.REVIEWS_CREDITS_EDIT_OWN,
            allow_jobs=True,
        )
    except ValueError as exc:
        db.rollback()
        raise root._review_mutation_error(exc) from exc


@router.get(
    "/api/credits-detections/{scan_id}/clip",
    responses={
        200: {
            "content": {"video/mp4": {}},
            "description": "Bounded credits boundary preview",
        },
        **_review_error_responses(404, 409, 413, 422, 500, 503),
    },
)
def api_credits_detection_clip(
    scan_id: str,
    boundary_seconds: float,
    caller: ReviewAccess = Depends(review_caller),
):
    """Render a short verified video clip around a credits boundary."""
    facts = scan_job_facts(caller.db, [scan_id]).get(scan_id)
    if facts is None:
        raise not_found()
    try:
        content = caller.create(
            facts.media,
            lambda session: render_credits_detection_preview(
                session, scan_id=scan_id, boundary_seconds=boundary_seconds
            ),
            capability=C.REVIEWS_PREVIEW_OWN,
            scan_ids=[scan_id],
            creating=False,
            allow_jobs=True,
        )
    except CreditsRouteError as exc:
        raise root._review_http_error(exc.code, str(exc), exc.status_code) from exc
    if content is None:
        raise root._review_http_error("scan_not_found", "Credits detection scan was not found", 404)
    return Response(
        content=content,
        media_type="video/mp4",
        headers={
            "Content-Disposition": 'inline; filename="credits-preview.mp4"',
            "Cache-Control": "no-store",
        },
    )
