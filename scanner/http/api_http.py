"""HTTP deployment snapshots, middleware, and launch configuration."""

import logging
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

from scanner.runtime.production_frontend import configure_frontend
from scanner.settings.config import settings
from scanner.settings.deployment_config import (
    DeploymentSnapshot,
    DeploymentStore,
    resolve_deployment_snapshot,
)

logger = logging.getLogger("scanner.api")


def _default_deployment_snapshot() -> DeploymentSnapshot:
    """Provide a deterministic snapshot for direct ASGI imports and tests.

    The supported CLI replaces it before the first request.  Keeping this direct
    path explicit preserves API-only/manual Uvicorn development while correctly
    reporting restart support later instead of fabricating controller support.
    """

    from scanner.runtime.restart_service import initial_snapshot

    if initial_snapshot is not None:
        return initial_snapshot
    return resolve_deployment_snapshot(
        store=DeploymentStore(settings.data_dir),
        auth_cookie_secure=settings.auth_cookie_secure,
        auth_cookie_secure_overridden=settings.is_environment_override("auth_cookie_secure"),
    )


def cors_options(snapshot: DeploymentSnapshot) -> dict[str, object]:
    """Build explicit CORS policy from an immutable service-generation snapshot."""
    # A credentialed browser response may only ever name the configured browser origin.
    # Uncredentialed integrations retain the existing explicit CORS allowlist.
    origins = (
        [snapshot.public_origin.serialized]
        if settings.cors_allow_credentials
        else settings.cors_origins
    )
    return {
        "allow_origins": origins,
        "allow_credentials": settings.cors_allow_credentials,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }


class PrefixRoutingMiddleware(BaseHTTPMiddleware):
    """Enforce the configured proxy-preserved mount before route/auth handling."""

    async def dispatch(self, request: Request, call_next):
        from scanner import api as root

        snapshot = root.active_deployment_snapshot(request.app)
        prefix = snapshot.prefix
        path = request.scope["path"]
        if prefix != "/":
            if path == prefix:
                query = request.scope["query_string"]
                location = f"{prefix}/"
                if query:
                    location = f"{location}?{query.decode('latin-1')}"
                return RedirectResponse(location, status_code=307)
            if not path.startswith(f"{prefix}/"):
                # Reject paths outside the configured prefix before frontend fallback.
                return Response(status_code=404)

        response = await call_next(request)
        location = response.headers.get("location")
        if location is not None and 300 <= response.status_code < 400:
            parsed = urlsplit(location)
            if parsed.path and (
                prefix == "/" or parsed.path == prefix or parsed.path.startswith(f"{prefix}/")
            ):
                # Starlette's automatic slash redirects use the request's internal
                # HTTP scheme/Host.  A path-only Location is safe behind TLS
                # termination and cannot be redirected to a spoofed authority.
                relative_location = parsed.path
                if parsed.query:
                    relative_location = f"{relative_location}?{parsed.query}"
                if parsed.fragment:
                    relative_location = f"{relative_location}#{parsed.fragment}"
                response.headers["location"] = relative_location
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Record compact request outcomes without logging query strings or request bodies."""

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("HTTP %s %s failed", request.method, request.url.path)
            raise
        elapsed_ms = round((time.perf_counter() - started) * 1_000)
        logger.info(
            "HTTP %s %s -> %d in %dms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response


def active_deployment_snapshot(application: FastAPI | None = None) -> DeploymentSnapshot:
    """Return the snapshot fixed for this HTTP-service generation."""

    from scanner import api as root

    return (application or root.app).state.deployment_snapshot


def configure_http_service(application: FastAPI, launch_context: object) -> None:
    """Freeze launch context before the HTTP middleware stack is constructed.

    ``launch_context`` is deliberately duck-typed to avoid a CLI/API import cycle.
    The standalone parent controller retains the same object across generations.
    Reconfiguration after a request is rejected: saved changes
    must wait for a new HTTP child instead of changing active auth policy in place.
    """

    if application.middleware_stack is not None:
        raise RuntimeError("HTTP service is already running; deployment changes require restart")
    snapshot = getattr(launch_context, "snapshot", None)
    if not isinstance(snapshot, DeploymentSnapshot):
        raise TypeError("HTTP launch context must contain a DeploymentSnapshot")
    application.state.deployment_snapshot = snapshot
    application.state.launch_context = launch_context
    configure_frontend(application, api_only=bool(getattr(launch_context, "api_only", False)))
    # FastAPI uses root_path for matching routes below a prefix and for OpenAPI
    # server metadata. PrefixRoutingMiddleware separately rejects root aliases.
    application.root_path = snapshot.prefix

    # CORS options are otherwise captured when FastAPI builds its middleware stack.
    # Replace only the existing CORS entry before first request; all auth middleware
    # reads request.app.state and therefore observes the same snapshot.
    for index, middleware in enumerate(application.user_middleware):
        if middleware.cls is CORSMiddleware:
            application.user_middleware[index] = middleware.__class__(
                CORSMiddleware, **cors_options(snapshot)
            )
            break


