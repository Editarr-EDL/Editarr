"""Global, fail-closed HTTP access, source-origin, and CSRF policy.

Every protected request needs an individual session or API key; no client
address or deployment mode stands in for one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from scanner.db import get_session_factory
from scanner.runtime.application_paths import application_relative_path, unique_cookie_value
from scanner.runtime.maintenance import MaintenanceUnavailable
from scanner.settings.config import Settings, settings
from scanner.settings.deployment_config import DeploymentSnapshot

from .access.api_shapes import ACCESS_ERROR_STATUS, AccessErrorCode
from .access.decisions import DENIAL_DETAILS, admission_error
from .access.identity import CredentialKind, UserId
from .access.request_access import AuthenticatedCredential, record_credential
from .access.route_match import interactive_only
from .api_keys import authenticate_api_key
from .auth_origin import (
    OriginParseError,
    PublicOrigin,
    direct_request_origin,
    is_local_bootstrap_host,
    normalize_header_value,
    parse_origin_header,
    parse_referer_header,
)
from .auth_repository import (
    AuthenticatedSession,
    authenticate_session,
    refresh_session_activity,
    setup_required,
    validate_csrf_token,
)
from .auth_routes import (
    AUTH_JELLYFIN_LOGIN_PATH,
    AUTH_PATHS,
    AUTH_QUICK_CONNECT_PATHS,
    AUTH_SETUP_PATH,
    AUTH_STATUS_PATH,
    DOCUMENTATION_PATHS,
    PROTECTED_ROUTE_PATHS,
    PUBLIC_STATIC_ROUTE_PATHS,
    RETIRED_SIGN_IN_PATHS,
    SAFE_METHODS,
    SETUP_RESTORE_PATHS,
    _setup_restore_request,
)


def session_now() -> datetime:
    """Return the time used for session issue, expiry, and refresh; tests replace it."""

    return datetime.now(timezone.utc)


def policy_response(status_code: int, code: str, detail: str) -> JSONResponse:
    """Return the fixed machine-readable access-policy error envelope."""

    return JSONResponse(status_code=status_code, content={"code": code, "detail": detail})


def _denied(code: AccessErrorCode) -> JSONResponse:
    response = policy_response(ACCESS_ERROR_STATUS[code], code.value, DENIAL_DETAILS[code])
    response.headers["Cache-Control"] = "no-store"
    return response


def _header_values(headers: Sequence[tuple[bytes, bytes]], name: bytes) -> list[bytes]:
    return [value for candidate, value in headers if candidate.lower() == name]


def _one_header(headers: Sequence[tuple[bytes, bytes]], name: bytes) -> str | None:
    values = _header_values(headers, name)
    if not values:
        return None
    if len(values) != 1:
        raise OriginParseError("repeated source header")
    return normalize_header_value(values[0])


def _source_origin_is_valid(
    headers: Sequence[tuple[bytes, bytes]],
    public_origin: PublicOrigin,
    *,
    unsafe: bool,
    allow_headless: bool,
    status_request: bool = False,
) -> bool:
    """Apply the approved Origin/Referer/Fetch Metadata decision table."""

    try:
        origin = _one_header(headers, b"origin")
        referer = _one_header(headers, b"referer")
        fetch_site = _one_header(headers, b"sec-fetch-site")
        fetch_metadata_present = any(name.lower().startswith(b"sec-fetch-") for name, _ in headers)
        if fetch_site is not None and fetch_site != "same-origin":
            return False
        if origin is not None:
            if "," in origin or parse_origin_header(origin) != public_origin:
                return False
        elif referer is not None:
            if "," in referer or parse_referer_header(referer) != public_origin:
                return False
        elif unsafe:
            return allow_headless and not fetch_metadata_present
        # Status is deliberately permissive only when no browser source headers are sent.
        return True
    except OriginParseError:
        return False


def _csrf_is_valid(headers: Sequence[tuple[bytes, bytes]], session: AuthenticatedSession) -> bool:
    values = _header_values(headers, b"x-csrf-token")
    if len(values) != 1:
        return False
    try:
        token = normalize_header_value(values[0])
    except OriginParseError:
        return False
    # The repository additionally rejects all noncanonical encodings before comparison.
    return "," not in token and validate_csrf_token(session, token)


class AuthPolicyMiddleware(BaseHTTPMiddleware):
    """Enforce policy before FastAPI resolves or enters an endpoint function."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        configuration: Settings = settings,
        deployment_snapshot: DeploymentSnapshot | None = None,
    ) -> None:
        super().__init__(app)
        self._configuration = configuration
        self._deployment_snapshot = deployment_snapshot

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Apply the active authentication policy to each incoming HTTP request."""
        if request.method == "TRACE":
            return Response(status_code=405)
        # CORS preflight contains no application request. CORS itself rejects untrusted
        # credentialed origins; allowing OPTIONS avoids treating it as a bypass route.
        if request.method == "OPTIONS":
            return await call_next(request)

        snapshot = getattr(request.app.state, "deployment_snapshot", self._deployment_snapshot)
        if snapshot is None:
            # Production startup installs an immutable snapshot before binding. Keep
            # direct ASGI invocation fail-closed without exposing configuration.
            return policy_response(
                503,
                "authentication_configuration_required",
                "Authentication configuration is unavailable",
            )
        factory = get_session_factory()
        db = factory()
        try:
            return await self._dispatch_with_database(request, call_next, db, snapshot)
        except MaintenanceUnavailable:
            response = policy_response(
                503, "database_maintenance", "Database maintenance is active; retry later"
            )
            response.headers["Cache-Control"] = "no-store"
            response.headers["Retry-After"] = "1"
            return response
        finally:
            db.close()

    async def _dispatch_with_database(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
        db: Any,
        snapshot: DeploymentSnapshot,
    ) -> Response:
        """Authorize a request against persisted credentials and session state."""
        path = application_relative_path(request, snapshot)
        if path is None:
            # Prefix routing rejects these before this middleware in supported
            # assembly.  Keep direct/in-process ASGI use fail-closed as well.
            return Response(status_code=404)
        public_origin = snapshot.public_origin
        headers = request.scope.get("headers", ())
        if snapshot.base_url_source == "local_default":
            try:
                authority = _one_header(headers, b"host")
                if authority is None:
                    raise OriginParseError("Host is required for direct access")
                origin = _one_header(headers, b"origin")
                referer = _one_header(headers, b"referer")
                scheme = request.scope.get("scheme", "http")
                if origin is not None:
                    scheme = parse_origin_header(origin).scheme
                elif referer is not None:
                    scheme = parse_referer_header(referer).scheme
                public_origin = direct_request_origin(scheme, authority)
            except OriginParseError:
                return policy_response(403, "origin_failed", "Request origin was rejected")
        request.state.authentication_origin = public_origin
        unsafe = request.method not in SAFE_METHODS
        now = session_now()
        token = unique_cookie_value(request, self._configuration.auth_cookie_name)
        session = authenticate_session(db, token, now=now) if token is not None else None
        if session is not None and _session_precedes_cutoff(session, snapshot):
            session = None
        session_denial = admission_error(session.status) if session is not None else None
        # Without a configured Base URL the origin follows Host, so first-run setup and
        # setup-restore refuse DNS names that could be rebound to this machine.
        rebindable_host = snapshot.base_url_source == "local_default" and not (
            is_local_bootstrap_host(public_origin.host)
        )

        # These bootstrap endpoints own their state-specific 409/423 responses, but
        # source checks must happen before body parsing or credential verification.
        if path in {AUTH_SETUP_PATH, AUTH_JELLYFIN_LOGIN_PATH, *RETIRED_SIGN_IN_PATHS}:
            if (path == AUTH_SETUP_PATH and rebindable_host) or not _source_origin_is_valid(
                headers, public_origin, unsafe=True, allow_headless=True
            ):
                return policy_response(403, "origin_failed", "Request origin was rejected")
            return await call_next(request)

        if path in AUTH_QUICK_CONNECT_PATHS:
            if not _source_origin_is_valid(
                headers, public_origin, unsafe=unsafe, allow_headless=True
            ):
                return policy_response(403, "origin_failed", "Request origin was rejected")
            return await call_next(request)

        if path == AUTH_STATUS_PATH:
            if session is not None and not _source_origin_is_valid(
                headers,
                public_origin,
                unsafe=False,
                allow_headless=True,
                status_request=True,
            ):
                return policy_response(403, "origin_failed", "Request origin was rejected")
            response = await call_next(request)
            if session is not None and session_denial is None and response.status_code < 400:
                refresh_session_activity(db, session, now=now, configuration=self._configuration)
            return response

        awaiting_setup = setup_required(db)
        if _setup_restore_request(path, request.method):
            if not awaiting_setup:
                return policy_response(
                    409, "setup_already_complete", "Administrator setup is complete"
                )
            if rebindable_host or not _source_origin_is_valid(
                headers, public_origin, unsafe=True, allow_headless=False
            ):
                return policy_response(403, "origin_failed", "Request origin was rejected")
            db.close()
            return await call_next(request)
        if awaiting_setup:
            return policy_response(423, "setup_required", "Administrator setup is required")

        key_headers = _header_values(headers, b"x-api-key")
        if key_headers:
            holder = (
                authenticate_api_key(db, key_headers[0].decode("latin-1"))
                if len(key_headers) == 1
                else None
            )
            if holder is None:
                return policy_response(401, "invalid_api_key", "API key was rejected")
            denial = admission_error(holder.status)
            if denial is None and interactive_only(request.app, request.scope):
                denial = AccessErrorCode.INTERACTIVE_SESSION_REQUIRED
            if denial is not None:
                return _denied(denial)
            if not _source_origin_is_valid(
                headers, public_origin, unsafe=unsafe, allow_headless=True
            ):
                return policy_response(403, "origin_failed", "Request origin was rejected")
            record_credential(
                request, AuthenticatedCredential(UserId(holder.user_id), CredentialKind.API_KEY)
            )
            # Restore activation must release the read lease before replacing the DB.
            db.close()
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response

        if session is None:
            return policy_response(401, "authentication_required", "Authentication is required")

        if session_denial is not None:
            return _denied(session_denial)
        record_credential(
            request,
            AuthenticatedCredential(
                UserId(session.user_id), CredentialKind.SESSION, session_id=session.id
            ),
        )

        if unsafe:
            if not _source_origin_is_valid(
                headers, public_origin, unsafe=True, allow_headless=False
            ):
                return policy_response(403, "origin_failed", "Request origin was rejected")
            if not _csrf_is_valid(headers, session):
                return policy_response(403, "csrf_failed", "Request verification failed")

        response = await call_next(request)
        if response.status_code < 400:
            refresh_session_activity(db, session, now=now, configuration=self._configuration)
        return response


def _session_precedes_cutoff(session: AuthenticatedSession, snapshot: DeploymentSnapshot) -> bool:
    """Reject only sessions older than an already-activated scope transition."""

    return (
        snapshot.session_not_before is not None and session.created_at < snapshot.session_not_before
    )


def route_policy_inventory(route_paths: Sequence[str]) -> set[str]:
    """Return unclassified routes; all app routes are intentionally protected or auth-public."""

    # The middleware's default is protected, including docs and unknown paths. Keeping
    # this function explicit makes additions visible to the inventory test.
    classified = (
        set(AUTH_PATHS)
        | set(SETUP_RESTORE_PATHS)
        | set(DOCUMENTATION_PATHS)
        | set(PROTECTED_ROUTE_PATHS)
    )
    return set(route_paths) - classified


def public_static_route_inventory(route_paths: Sequence[str]) -> set[str]:
    """Return middleware static paths not covered by the approved public inventory."""

    return set(route_paths) - set(PUBLIC_STATIC_ROUTE_PATHS)
