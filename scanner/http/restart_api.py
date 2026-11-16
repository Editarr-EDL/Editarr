"""Protected restart endpoints; acknowledgment precedes control-channel commit."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from scanner.models import RestartCapability
from scanner.security.access.capability_ids import Capability as C
from scanner.security.audit.actions import AuditAction
from scanner.settings.deployment_config import (
    DeploymentConfigurationError,
    DeploymentConfigurationUnavailable,
)

from .deployment_api import (
    _next_start_snapshot,
    _restart_capability,
    _store,
    _unavailable_response,
)
from .operations_access import Operator, operation

router = APIRouter()


@router.get(
    "/api/system/restart",
    response_model=RestartCapability,
    dependencies=[Depends(operation(C.SYSTEM_RESTART))],
)
def get_restart(request: Request):
    return _restart_capability(request)


@router.post("/api/system/restart", status_code=202)
def post_restart(
    request: Request,
    caller: Operator = Depends(operation(C.SYSTEM_RESTART, AuditAction.RESTART)),
):
    """Validate a restart request and ask the controller to activate pending settings."""
    client = getattr(request.app.state, "restart_client", None)
    if client is None:
        capability = _restart_capability(request)
        detail = (
            "In-app restart is unavailable under Uvicorn development reload; "
            "use file reload or restart the development command"
            if capability.reason == "development_reload"
            else "Restart Editarr requires the standalone editarr launcher"
        )
        return JSONResponse(
            status_code=409,
            content={
                "code": "restart_unsupported",
                "detail": detail,
            },
        )
    try:
        snapshot = _next_start_snapshot(request, _store().read())
    except (DeploymentConfigurationError, DeploymentConfigurationUnavailable):
        return _unavailable_response()
    error = client.reserve()
    if error:
        return JSONResponse(
            status_code=503 if error == "restart_unavailable" else 409,
            content={
                "code": error,
                "detail": (
                    "The standalone controller is unavailable"
                    if error == "restart_unavailable"
                    else "A restart is already in progress"
                ),
            },
        )
    request.scope["editarr.restart_after_response"] = client.commit
    request.scope["editarr.restart_cancel_response"] = client.cancel
    caller.record(AuditAction.RESTART, {})
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "target_generation": client.capability.target_generation,
            "reconnect_url": f"{snapshot.base_url}/#/settings/general",
        },
    )
