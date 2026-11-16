"""Protected, staged deployment-settings endpoints.

These handlers deliberately operate on the deployment file and a separately
resolved next-start snapshot.  They never alter the snapshot installed on the
running FastAPI application; the restart controller applies changes in a new HTTP
service generation.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from scanner.models import (
    DeploymentConfiguration,
    DeploymentSavedOverrides,
    DeploymentSnapshotDto,
    DeploymentUpdate,
    RestartCapability,
)
from scanner.security.access.capability_ids import Capability as C
from scanner.security.audit.actions import AuditAction
from scanner.settings.config import settings
from scanner.settings.deployment_config import (
    CookieScope,
    DeploymentConfigurationError,
    DeploymentConfigurationUnavailable,
    DeploymentOverrides,
    DeploymentSnapshot,
    DeploymentStore,
    resolve_deployment_snapshot,
)

from .operations_access import Operator, operation

router = APIRouter()


def _snapshot_dto(snapshot: DeploymentSnapshot) -> DeploymentSnapshotDto:
    return DeploymentSnapshotDto(
        base_url=snapshot.base_url,
        public_origin=snapshot.public_origin.serialized,
        prefix=snapshot.prefix,
        listening_port=snapshot.listening_port,
        base_url_source=snapshot.base_url_source,
        port_source=snapshot.port_source,
        cli_port_override=snapshot.cli_port_override,
    )


def _restart_capability(request: Request) -> RestartCapability:
    """Return the live restart capability or a truthful unsupported state."""

    capability = getattr(request.app.state, "restart_capability", None)
    if isinstance(capability, RestartCapability):
        return capability
    if isinstance(capability, dict):
        try:
            return RestartCapability.model_validate(capability)
        except ValueError:
            # A partial controller state must not claim restart support.
            pass
    return RestartCapability(
        supported=False,
        mode="unsupported",
        generation=0,
        in_progress=False,
        outcome="idle",
        reason="manual_uvicorn",
        detail="Restart Editarr requires the standalone editarr launcher",
    )


def _store() -> DeploymentStore:
    return DeploymentStore(settings.data_dir)


def _next_start_snapshot(
    request: Request, overrides: DeploymentOverrides
) -> DeploymentSnapshot:
    """Use the launch context's durable CLI policy, never request headers."""

    active = request.app.state.deployment_snapshot
    context = getattr(request.app.state, "launch_context", None)
    host = getattr(context, "host", "127.0.0.1")
    cli_port_override = getattr(context, "cli_port_override", active.cli_port_override)
    development = getattr(request.app.state, "development_topology", None)
    if development is not None:
        from scanner.runtime.development import resolve_development_topology

        return resolve_development_topology(
            configuration=settings,
            overrides=overrides,
            api_host=development.api_host,
            api_port=cli_port_override,
            web_host=development.web_host,
            web_port=development.web_port,
        ).snapshot
    return resolve_deployment_snapshot(
        store=_store(),
        overrides=overrides,
        cli_port_override=cli_port_override,
        bind_host=host,
        auth_cookie_secure=settings.auth_cookie_secure,
        auth_cookie_secure_overridden=settings.is_environment_override("auth_cookie_secure"),
    )


def _configuration_response(
    request: Request,
    overrides: DeploymentOverrides,
    next_start: DeploymentSnapshot,
) -> DeploymentConfiguration:
    """Serialize the active and pending deployment configuration for the API."""
    active = request.app.state.deployment_snapshot
    # Sources describe provenance, not active runtime behavior.  Saving the same
    # canonical URL as the current local/environment URL is intentionally not a
    # restart requirement, while a CLI port override masks a saved port change.
    restart_required = (
        active.base_url != next_start.base_url
        or active.listening_port != next_start.listening_port
        or active.cookie_secure != next_start.cookie_secure
    )
    return DeploymentConfiguration(
        active=_snapshot_dto(active),
        next_start=_snapshot_dto(next_start),
        saved=DeploymentSavedOverrides(base_url=overrides.base_url, port=overrides.port),
        restart_required=restart_required,
        restart=_restart_capability(request),
    )


def _unavailable_response() -> JSONResponse:
    # Store exceptions can contain private absolute paths or operating-system text.
    # The operator knows this is deployment storage without the response disclosing it.
    return JSONResponse(
        status_code=503,
        content={
            "code": "deployment_configuration_unavailable",
            "detail": "Deployment settings are temporarily unavailable",
        },
    )


def _invalid_response(error: DeploymentConfigurationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"code": "deployment_configuration_invalid", "detail": str(error)},
    )


def _scope_changed(active: DeploymentSnapshot, next_start: DeploymentSnapshot) -> bool:
    """Return whether an explicit Base URL edit changes canonical URL scope."""

    return (
        active.public_origin != next_start.public_origin or active.prefix != next_start.prefix
    )


@router.get(
    "/api/settings/deployment",
    response_model=DeploymentConfiguration,
    dependencies=[Depends(operation(C.DEPLOYMENT_VIEW))],
)
def get_deployment_configuration(request: Request):
    """Return active state, explicit saved fields, and independently resolved next state."""

    store = _store()
    try:
        overrides = store.read()
        next_start = _next_start_snapshot(request, overrides)
    except (DeploymentConfigurationError, DeploymentConfigurationUnavailable):
        return _unavailable_response()
    return _configuration_response(request, overrides, next_start)


@router.put("/api/settings/deployment", response_model=DeploymentConfiguration)
def update_deployment_configuration(
    request: Request,
    update: DeploymentUpdate,
    caller: Operator = Depends(operation(C.DEPLOYMENT_EDIT, AuditAction.DEPLOYMENT_CHANGE)),
):
    """Persist a complete valid desired state without hot-applying it.

    Only an explicit Base URL field can stage a session-scope transition.  A port
    save may change a local effective origin on the next launch, but must retain
    forms sessions under the approved port-only rule.
    """

    store = _store()
    try:
        current = store.read()
    except (DeploymentConfigurationError, DeploymentConfigurationUnavailable):
        return _unavailable_response()

    fields = update.model_fields_set
    update_values: dict[str, object] = {
        field: getattr(update, field) for field in ("base_url", "port") if field in fields
    }
    try:
        proposed = store.preview_update(current, **update_values)
        proposed_next_start = _next_start_snapshot(request, proposed)
    except DeploymentConfigurationError as error:
        return _invalid_response(error)
    except DeploymentConfigurationUnavailable:
        return _unavailable_response()

    cookie_scope: CookieScope | None = None
    if "base_url" in fields:
        # Do not touch a prior pending transition for a port-only update.  If the
        # Base URL is deliberately changed back to the active scope, clear its stale
        # pending marker rather than invalidating sessions on a no-op restart.
        cookie_scope = replace(
            current.cookie_scope,
            pending_previous_path=(
                request.app.state.deployment_snapshot.prefix
                if _scope_changed(request.app.state.deployment_snapshot, proposed_next_start)
                else None
            ),
        )
        try:
            proposed = store.preview_update(
                current, **update_values, cookie_scope=cookie_scope
            )
            proposed_next_start = _next_start_snapshot(request, proposed)
        except DeploymentConfigurationError as error:
            return _invalid_response(error)
        except DeploymentConfigurationUnavailable:
            return _unavailable_response()

    try:
        if cookie_scope is not None:
            update_values["cookie_scope"] = cookie_scope
        persisted = store.update(**update_values)
        next_start = _next_start_snapshot(request, persisted)
    except DeploymentConfigurationError:
        # Incoming field validation occurred above.  An error here means the saved
        # state changed underneath us and cannot safely be interpreted as a client
        # validation failure.
        return _unavailable_response()
    except DeploymentConfigurationUnavailable:
        return _unavailable_response()
    caller.record(
        AuditAction.DEPLOYMENT_CHANGE,
        {"base_url": "base_url" in fields, "port": "port" in fields},
    )
    return _configuration_response(request, persisted, next_start)
