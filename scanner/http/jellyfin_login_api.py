"""Jellyfin sign-in by password: prove, admit, and issue a session.

Shares the cookie, CSRF, session lifetime, audit, and limiter policy of
`auth_api`. The username only selects which Jellyfin user to try; the
account is found by the linked server and Jellyfin user ID that Jellyfin returns.
Quick Connect sign-in (`jellyfin_quick_connect_api`) ends in `_issue_session` too.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import UserRow, get_db
from scanner.integrations.jellyfin_user_snapshot import CredentialProof, Unverified
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security import auth_policy
from scanner.security.access.records import AuditOutcome
from scanner.security.access.vocabulary import Role
from scanner.security.audit import authentication as stored_audit
from scanner.security.auth_repository import issue_session, setup_required
from scanner.security.jellyfin_accounts.admission import (
    SignInRefusal,
    SignInRefused,
    encodable,
    sign_in,
)
from scanner.security.jellyfin_accounts.sync import open_adapter

from .auth_api_models import AuthenticationStatusResponse, LoginRequest
from .auth_rate_limit import LoginRateLimiter
from .auth_request_audit import audit_context, authentication_log, record_throttled
from .auth_status import signed_in

router = APIRouter(prefix="/api/auth", tags=["authentication"])

jellyfin_login_limiter = LoginRateLimiter()
"""Failed Jellyfin sign-ins per typed name and client address, on the sign-in limits."""

NAME_LIMIT, PASSWORD_LIMIT = 256, 4096
"""Longer values are refused as invalid credentials without contacting Jellyfin."""
_INVALID = SignInRefusal.INVALID_CREDENTIALS
_REFUSALS = {
    SignInRefusal.INVALID_CREDENTIALS: (401, "Unable to sign in with those credentials"),
    SignInRefusal.JELLYFIN_UNAVAILABLE: (503, "Jellyfin could not verify the sign-in"),
    SignInRefusal.ACCOUNT_DISABLED: (403, "This account is disabled"),
    SignInRefusal.JELLYFIN_USER_BLOCKED: (
        403,
        "This Jellyfin user is blocked from Editarr; the owner can unblock them in Users",
    ),
}


def _bucket(username: str) -> str:
    return "jellyfin:" + username.casefold().encode("utf-8", "backslashreplace").decode()


def _setup_required(db: Session) -> Response | None:
    from . import auth_api

    if setup_required(db):
        return auth_api._error(423, "setup_required", "Administrator setup is required")
    return None


def _not_configured() -> Response:
    from . import auth_api

    return auth_api._error(409, "jellyfin_not_configured", "Jellyfin sign-in is unavailable")


def _log_sign_in(
    request: Request,
    *,
    now: datetime,
    status: int,
    refusal: SignInRefusal | None = None,
    role: Role | None = None,
    user_id: int | None = None,
    session_id: int | None = None,
) -> None:
    """Write one Jellyfin sign-in outcome to the authentication log, as local sign-in did."""

    from . import auth_api

    if (log := authentication_log(request)) is None:
        return
    context = audit_context(request, auth_api._client_address(request).address)
    log.emit(
        event="auth.login",
        outcome="success" if refusal is None else "failure" if refusal is _INVALID else "denied",
        severity="info" if refusal is None else "warning",
        context=replace(context, http_status=status),
        actor="anonymous" if role is None else "administrator" if role is Role.OWNER else "user",
        admin_user_id=user_id,
        session_id=session_id,
        reason_code=None if refusal is None else refusal.value,
        now=now,
    )


def _issue_session(
    db: Session,
    request: Request,
    response: Response,
    adapter: JellyfinUserAdapter,
    prove: Callable[[], CredentialProof | Unverified],
    *,
    remember_me: bool,
    return_to: str | None,
    now: datetime,
    refusals: dict[SignInRefusal, tuple[int, str]],
    method: stored_audit.SignInMethod,
    rejected: Callable[[], None] = lambda: None,
    credentials: tuple[str, ...] = (),
) -> Response | AuthenticationStatusResponse:
    """Admit the user `prove` proves and issue its session, or answer the refusal.

    Password and Quick Connect sign-in both end here, so they share admission,
    the disabled-account refusal, audit, and cookies. `rejected` runs when
    Jellyfin refused the proof. `credentials` that cannot be encoded are refused
    before the sign-in starts a Jellyfin read.
    """

    from . import auth_api

    def issue(user_id: int):
        session = issue_session(
            db,
            remember_me=remember_me,
            user_id=user_id,
            now=now,
            configuration=auth_api.settings,
            commit=False,
        )
        stored_audit.signed_in(db, user_id, remember_me=remember_me, now=now, method=method)
        return user_id, session

    try:
        with adapter:
            if not encodable(*credentials):
                raise SignInRefused(_INVALID)
            user_id, session = sign_in(
                db, adapter, prove, clock=auth_policy.session_now, issue=issue
            )
    except SignInRefused as refused:
        db.rollback()
        invalid = refused.refusal is _INVALID
        if invalid:
            rejected()
        stored_audit.sign_in_refused(
            db,
            AuditOutcome.FAILED if invalid else AuditOutcome.DENIED,
            refused.refusal.value,
            target=refused.user_id,
            now=now,
            method=method,
        )
        status, detail = refusals[refused.refusal]
        _log_sign_in(request, now=now, status=status, refusal=refused.refusal)
        return auth_api._error(status, refused.refusal.value, detail)
    _log_sign_in(
        request,
        now=now,
        status=200,
        role=Role(db.scalar(select(UserRow.role).where(UserRow.id == user_id))),
        user_id=user_id,
        session_id=session.id,
    )
    auth_api._set_session_cookie(
        response, session.token, remember_me=session.remember_me, request=request
    )
    auth_api._set_no_store(response)
    return signed_in(
        db,
        request,
        user_id=user_id,
        session_id=session.id,
        csrf_token=session.csrf_token,
        return_to=return_to,
    )


@router.post("/jellyfin/login", response_model=AuthenticationStatusResponse)
def jellyfin_login(
    payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
):
    """Issue a session for a valid Jellyfin user, creating its account when admission allows."""

    from . import auth_api

    if (refused := _setup_required(db)) is not None:
        return refused
    address = auth_api._client_address(request).throttle_key
    bucket = _bucket(payload.username)
    now = auth_policy.session_now()
    retry_after = jellyfin_login_limiter.retry_after(username=bucket, address=address)
    if retry_after:
        record_throttled(
            db,
            request,
            jellyfin_login_limiter,
            username=bucket,
            address=address,
            client_address=auth_api._client_address(request).address,
            now=now,
            method="password",
        )
        throttled = auth_api._error(
            429, "login_throttled", "Unable to sign in with those credentials"
        )
        throttled.headers["Retry-After"] = str(retry_after)
        auth_api._set_no_store(throttled)
        return throttled
    password = payload.password.get_secret_value()
    if len(payload.username) > NAME_LIMIT or len(password) > PASSWORD_LIMIT:
        jellyfin_login_limiter.record_failure(username=bucket, address=address)
        _log_sign_in(request, now=now, status=401, refusal=_INVALID)
        return auth_api._error(401, "invalid_credentials", _REFUSALS[_INVALID][1])
    adapter = open_adapter(db)
    if adapter is None:
        return _not_configured()
    answer = _issue_session(
        db,
        request,
        response,
        adapter,
        lambda: adapter.authenticate(payload.username, password),
        remember_me=payload.remember_me,
        return_to=payload.return_to,
        now=now,
        refusals=_REFUSALS,
        method="password",
        rejected=lambda: jellyfin_login_limiter.record_failure(username=bucket, address=address),
        credentials=(payload.username, password),
    )
    if isinstance(answer, AuthenticationStatusResponse):
        jellyfin_login_limiter.clear_username(bucket)
    return answer
