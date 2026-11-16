"""FastAPI adapter: the credential recorded by the middleware and the live request principal.

The access middleware authenticates and admits a session or API key, then
records an `AuthenticatedCredential`. Handlers depend on `request_access`,
which re-reads the live principal, and call `authorization.require` with the
resolved target. `install_access_errors` renders `AccessDenied` in the
access-policy envelope.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from scanner.db import get_db

from .api_shapes import AccessErrorCode
from .authorization import require_principal
from .decisions import DENIAL_DETAILS, AccessDenied
from .grants import PrincipalAccess
from .identity import CredentialKind, UserId


@dataclass(frozen=True)
class AuthenticatedCredential:
    """Who the middleware authenticated for this request, and with what credential."""

    user_id: UserId
    kind: CredentialKind
    session_id: int | None = None


def record_credential(request: Request, credential: AuthenticatedCredential) -> None:
    request.state.access_credential = credential


def request_access(request: Request, db: Session = Depends(get_db)) -> PrincipalAccess:
    """FastAPI dependency: the live principal behind this request's own credential.

    A request admitted only by a legacy authentication bypass has no
    credential, so it is refused rather than treated as the owner.
    """

    credential = getattr(request.state, "access_credential", None)
    if not isinstance(credential, AuthenticatedCredential):
        raise AccessDenied(AccessErrorCode.AUTHENTICATION_REQUIRED)
    return require_principal(db, credential.user_id, credential.kind, credential.session_id)


def denial_response(
    status_code: int,
    code: str,
    detail: str,
    *,
    headers: Mapping[str, str] | None = None,
    retry_after: int | None = None,
) -> JSONResponse:
    """Render an authentication or access refusal with its optional retry window."""
    response_headers = {"Cache-Control": "no-store", **(headers or {})}
    if retry_after is not None:
        response_headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "detail": detail},
        headers=response_headers,
    )


def access_denied_response(code: AccessErrorCode | AccessDenied) -> JSONResponse:
    error = code if isinstance(code, AccessDenied) else AccessDenied(code)
    return denial_response(
        error.status_code, error.code.value, DENIAL_DETAILS[error.code], headers=error.headers
    )


def install_access_errors(application: FastAPI) -> None:
    """Render every `AccessDenied` in the `{"code", "detail"}` envelope with no-store."""

    async def handle(_request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, AccessDenied)
        return access_denied_response(error)

    application.add_exception_handler(AccessDenied, handle)
