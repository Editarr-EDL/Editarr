"""Authenticated model storage and acquisition endpoints.

Anyone allowed to download models may start an acquisition, but only a caller
who may view the inventory receives the artifact list in a response. The
acquisition runs for its initiator, who must still hold `models.download`
when it fetches and when it installs.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from scanner import api as root
from scanner.db import get_db
from scanner.model_acquisition import AcquisitionError, artifact_ids_for_backend
from scanner.models import (
    ModelActionResult,
    ModelPrefetchRequest,
    ModelStorageStatus,
    ScanMode,
)
from scanner.security.access.capability_ids import Capability as C
from scanner.security.audit.actions import AuditAction
from scanner.security.scan_jobs.authority import JobRefusedError
from scanner.security.scan_jobs.operation_gate import OperationGate
from scanner.settings.capabilities import current_environment
from scanner.settings.config import settings
from scanner.settings.model_scan_settings import (
    ModelScanSettings,
    ModelScanSettingsError,
    load_model_scan_settings,
    save_model_scan_settings,
)

from . import background
from .operations_access import Operator, operation

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/api/settings/models/scans",
    dependencies=[Depends(operation(C.MODELS_INVENTORY_VIEW))],
)
def api_get_model_scan_settings() -> ModelScanSettings:
    """Read the instance settings applied to future scans."""
    try:
        return load_model_scan_settings(
            settings.data_dir, default_audio_padding=settings.profanity_pad_seconds
        )
    except ModelScanSettingsError as error:
        raise HTTPException(503, str(error)) from error


@router.put("/api/settings/models/scans")
def api_save_model_scan_settings(
    update: ModelScanSettings,
    caller: Operator = Depends(operation(C.DEPLOYMENT_EDIT, AuditAction.SCAN_SETTINGS_CHANGE)),
) -> ModelScanSettings:
    """Save the instance's preferred settings for future scans."""
    try:
        save_model_scan_settings(settings.data_dir, update)
    except ModelScanSettingsError as error:
        raise HTTPException(503, str(error)) from error
    caller.record(AuditAction.SCAN_SETTINGS_CHANGE, {})
    return update


def _model_storage_status() -> dict[str, object]:
    """Build the authenticated model view from the manifest and capability truth."""

    service = root.get_acquisition_service()
    environment = current_environment()

    def device_for(artifact_id: str) -> Literal["cpu"] | None:
        if artifact_id.startswith(("wd-", "siglip2-moderation-head-")):
            return "cpu"
        return None

    return {
        "artifacts": [
            {
                "artifact_id": status.artifact_id,
                "backend_ids": list(status.backend_ids),
                "device": device_for(status.artifact_id),
                "state": status.download_state(),
                "expected_size": status.bytes_total,
                "source_url": status.source_url,
                "license_spdx": status.license_spdx,
                "license_status": status.license_status,
                "removable": status.state != "not_required",
            }
            for status in service.list_artifacts()
        ],
        "model_capabilities": list(root.model_capabilities(environment)),
    }


def _model_error(error: AcquisitionError, *, status_code: int = 409) -> Response:
    """Keep acquisition failures actionable while never exposing transport secrets."""

    from fastapi.responses import JSONResponse

    code = error.code
    if code in {"network_error", "offline_missing"}:
        status_code = 503
    return JSONResponse(status_code=status_code, content={"code": code, "detail": error.detail})


def _model_code_error(status_code: int, code: str, detail: str) -> Response:
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"code": code, "detail": detail})


def _backend_is_available(backend_id: str) -> bool:
    try:
        root.require_available(backend_id)
        return True
    except ValueError:
        return False


def _active_model_backends(db: Session) -> set[str]:
    """Backends held by workers that may currently read model files."""

    active: set[str] = set()
    for scan in root.list_scan_queue(db):
        if scan.status not in {"acquiring_model", "scanning", "cancelling"}:
            continue
        if scan.scan_audio and scan.audio_model:
            active.add(scan.audio_model)
        if scan.scan_video and scan.video_model:
            active.add(scan.video_model)
        if (
            scan.scan_mode == ScanMode.CREDITS_DETECTION
            and scan.credits_detection is not None
            and scan.credits_detection.configuration.qwen_model is not None
        ):
            active.add("qwen3_vl")
    return active


def _action_result(caller: Operator, *, accepted: bool, removed: bool) -> dict[str, object]:
    status = _model_storage_status() if caller.may(C.MODELS_INVENTORY_VIEW) else None
    return {"status": status, "accepted": accepted, "removed": removed}


@router.get(
    "/api/models",
    response_model=ModelStorageStatus,
    dependencies=[Depends(operation(C.MODELS_INVENTORY_VIEW))],
)
def api_model_storage():
    """Return authenticated, manifest-backed storage state with no cache paths."""

    return _model_storage_status()


@router.post("/api/models/prefetch", response_model=ModelActionResult, status_code=202)
def api_prefetch_model(
    request: ModelPrefetchRequest, caller: Operator = Depends(operation(C.MODELS_DOWNLOAD))
):
    """Start one deduplicated background acquisition for an available backend."""

    try:
        artifact_ids = artifact_ids_for_backend(request.backend_id)
    except ValueError:
        return _model_code_error(404, "unknown_backend", "Unknown model backend.")
    if not _backend_is_available(request.backend_id):
        return _model_code_error(
            409,
            "platform_unavailable",
            "This model backend is unavailable on this platform.",
        )
    if not artifact_ids:
        return _action_result(caller, accepted=False, removed=False)

    tasks = root._model_tasks()
    gate = OperationGate.for_request(caller.access, caller.db, C.MODELS_DOWNLOAD)
    with root._model_task_lock():
        existing = tasks.get(request.backend_id)
        if existing is not None and existing.is_alive():
            return _action_result(caller, accepted=False, removed=False)

        def prefetch() -> None:
            try:
                root.get_acquisition_service().prefetch_backend(request.backend_id)
            except AcquisitionError:
                # The acquisition service writes a safe, inspectable terminal state.
                pass
            except JobRefusedError:
                logger.info(
                    "Model download for %s stopped: its initiator may no longer download models",
                    request.backend_id,
                )
            finally:
                with root._model_task_lock():
                    tasks.pop(request.backend_id, None)

        tasks[request.backend_id] = background.start_thread(
            f"editarr-model-{request.backend_id}", gate, prefetch
        )
    return _action_result(caller, accepted=True, removed=False)


@router.post("/api/models/{artifact_id}/cancel", response_model=ModelActionResult)
def api_cancel_model_download(
    artifact_id: str, caller: Operator = Depends(operation(C.MODELS_DOWNLOAD_CANCEL))
):
    try:
        root.get_acquisition_service().request_cancel(artifact_id)
    except ValueError:
        return _model_code_error(404, "unknown_artifact", "Unknown managed model artifact.")
    except AcquisitionError as error:
        return _model_error(error)
    return _action_result(caller, accepted=True, removed=False)


@router.delete("/api/models/{artifact_id}", response_model=ModelActionResult)
def api_remove_model(
    artifact_id: str,
    db: Session = Depends(get_db),
    caller: Operator = Depends(operation(C.MODELS_DELETE)),
):
    """Remove exactly one idle regenerable artifact, never durable application data."""

    try:
        service = root.get_acquisition_service()
        artifact = service.manifest.artifact(artifact_id)
    except ValueError:
        return _model_code_error(404, "unknown_artifact", "Unknown managed model artifact.")
    if service.status(artifact_id).state == "downloading":
        return _model_code_error(
            409,
            "acquisition_active",
            "Cancel the active download before removing this model.",
        )
    if set(artifact.backend_ids) & _active_model_backends(db):
        return _model_code_error(
            409,
            "model_in_use",
            "Wait for the active scan to finish or cancel it before removing this model.",
        )
    try:
        removed = service.remove(artifact_id)
    except AcquisitionError as error:
        return _model_error(error)
    return _action_result(caller, accepted=False, removed=removed)
