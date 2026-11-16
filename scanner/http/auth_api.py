"""Authentication status, sign-out, and the cookie and response helpers sign-in routes share."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.runtime.application_paths import unique_cookie_value
from scanner.security import auth_policy
from scanner.security.access.decisions import admission_error
from scanner.security.access.request_access import denial_response
from scanner.security.auth import logout
from scanner.security.auth_audit import AuditContext
from scanner.security.auth_crypto import load_csrf_encryption_key
from scanner.security.auth_network import ClientAddressResolution, resolve_client_address
from scanner.security.auth_policy import _session_precedes_cutoff
from scanner.security.auth_repository import (
    AuthenticatedSession,
    authenticate_session,
    recover_csrf_token,
    refresh_session_activity,
    setup_required,
)
from scanner.settings.config import settings

from .api_key_api import register_api_key_routes
from .auth_api_models import AuthenticationStatusResponse
from .auth_request_audit import audit_context, authentication_log
from .auth_status import signed_in, signed_out
from .jellyfin_setup_api import register_setup_routes

router = APIRouter(prefix="/api/auth", tags=["authentication"])
register_api_key_routes(router)
register_setup_routes(router)


def _error(status_code: int, code: str, detail: str, *, retry_after: int | None = None) -> Response:
    return denial_response(status_code, code, detail, retry_after=retry_after)


def _client_address(request: Request) -> ClientAddressResolution:
    return resolve_client_address(
        request.scope.get("client"),
        request.scope.get("headers", ()),
        settings.auth_trusted_proxy_networks,
    )


def _audit_context(request: Request) -> AuditContext:
    return audit_context(request, _client_address(request).address)


def _current_session(request: Request, db: Session) -> AuthenticatedSession | None:
    """Return this request's own admitted session, read on the session clock."""

    token = unique_cookie_value(request, settings.auth_cookie_name)
    if token is None:
        return None
    session = authenticate_session(db, token, now=auth_policy.session_now())
    if session is None:
        return None
    snapshot = getattr(request.app.state, "deployment_snapshot", None)
    if snapshot is not None and _session_precedes_cutoff(session, snapshot):
        return None
    if admission_error(session.status):
        return None
    return session


def _status_response(
    db: Session,
    request: Request,
    *,
    response: Response | None = None,
    return_to: str | None = None,
) -> AuthenticationStatusResponse:
    """Describe setup state and this request's own session, never another account."""
    if setup_required(db):
        return signed_out(db, request, "setup_required", return_to=return_to)
    session = _current_session(request, db)
    csrf_token = (
        recover_csrf_token(
            db, session, csrf_encryption_key=load_csrf_encryption_key(settings.data_dir)
        )
        if session is not None
        else None
    )
    if session is None or csrf_token is None:
        return signed_out(db, request, "unauthenticated", return_to=return_to)
    refresh_session_activity(db, session, now=auth_policy.session_now(), configuration=settings)
    if response is not None:
        _set_no_store(response)
    return signed_in(
        db,
        request,
        user_id=session.user_id,
        session_id=session.id,
        csrf_token=csrf_token,
        return_to=return_to,
    )


def _set_no_store(response: Response) -> None:
    """Keep CSRF-bearing responses out of browser and intermediary caches."""

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _cookie_scope(request: Request) -> tuple[bool, str]:
    """Return cookie policy from the HTTP generation's immutable deployment snapshot.

    A bare router remains useful in narrowly scoped tests and API-only embedding;
    that legacy embedding has no configured prefix and therefore falls back to the
    explicit authentication setting. Supported service launches always install the
    snapshot before accepting requests.
    """

    snapshot = getattr(request.app.state, "deployment_snapshot", None)
    if snapshot is None:
        return settings.auth_cookie_secure, "/"
    if snapshot.base_url_source == "local_default":
        origin = getattr(request.state, "authentication_origin", snapshot.public_origin)
        return origin.scheme == "https", snapshot.prefix
    return snapshot.cookie_secure, snapshot.prefix


def _cookie_cleanup_paths(request: Request, active_path: str) -> tuple[str, ...]:
    """Return every local cookie path that must be expired before a new session."""

    snapshot = getattr(request.app.state, "deployment_snapshot", None)
    legacy_paths = snapshot.legacy_cookie_paths if snapshot is not None else ()
    return tuple(dict.fromkeys(("/", *legacy_paths, active_path)))


def _expire_session_cookies(response: Response, *, request: Request) -> None:
    secure, active_path = _cookie_scope(request)
    for path in _cookie_cleanup_paths(request, active_path):
        response.delete_cookie(
            key=settings.auth_cookie_name,
            httponly=True,
            secure=secure,
            samesite=settings.auth_cookie_samesite,
            path=path,
        )


def _set_session_cookie(
    response: Response, token: str, *, remember_me: bool, request: Request
) -> None:
    """Set a scoped session cookie using the active deployment security settings."""
    secure, path = _cookie_scope(request)
    # A transition can leave same-name root and prefix cookies in a browser.  Expire
    # every known local path first so a newly-issued active cookie is unambiguous.
    if getattr(request.app.state, "deployment_snapshot", None) is not None:
        _expire_session_cookies(response, request=request)
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_remember_me_seconds if remember_me else None,
        httponly=True,
        secure=secure,
        samesite=settings.auth_cookie_samesite,
        path=path,
    )


def _clear_session_cookie(response: Response, *, request: Request) -> None:
    _expire_session_cookies(response, request=request)


@router.get("/status", response_model=AuthenticationStatusResponse)
def auth_status(request: Request, response: Response, db: Session = Depends(get_db)):
    """Return safe setup/authentication state without exposing credentials."""

    return _status_response(db, request, response=response)


@router.post("/logout", response_model=AuthenticationStatusResponse)
def auth_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Revoke only this browser's session and clear exactly its cookie."""

    session = _current_session(request, db)
    if session is None:
        return _error(401, "authentication_required", "Authentication is required")
    logout(
        db,
        session,
        audit=authentication_log(request),
        context=_audit_context(request),
        now=auth_policy.session_now(),
    )
    _clear_session_cookie(response, request=request)
    return signed_out(db, request, "unauthenticated")
