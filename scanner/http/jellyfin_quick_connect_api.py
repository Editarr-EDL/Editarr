"""Jellyfin Quick Connect sign-in: start a code, watch for approval, and sign in once.

A Quick Connect attempt belongs to the browser that started it: the browser
holds an opaque HttpOnly handle and Jellyfin's secret stays in `quick_connect`.
An approved attempt signs in through `jellyfin_login_api._issue_session`, as
password sign-in does.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.integrations.jellyfin_quick_connect import (
    authenticate_quick_connect,
    initiate_quick_connect,
    quick_connect_enabled,
    quick_connect_request,
)
from scanner.integrations.jellyfin_user_snapshot import Unverified, UnverifiedReason
from scanner.runtime.application_paths import unique_cookie_value
from scanner.security import auth_policy
from scanner.security.access.records import AuditOutcome
from scanner.security.audit import authentication as stored_audit
from scanner.security.jellyfin_accounts.quick_connect import QuickConnectAttempts, RecentAnswer
from scanner.security.jellyfin_accounts.sync import open_adapter
from scanner.settings.config import settings

from .auth_api_models import (
    AuthenticationStatusResponse,
    QuickConnectAttemptResponse,
    QuickConnectAvailability,
    QuickConnectSignInRequest,
)
from .auth_request_audit import audit_context, authentication_log
from .jellyfin_login_api import (
    _INVALID,
    _REFUSALS,
    _issue_session,
    _not_configured,
    _setup_required,
)

router = APIRouter(prefix="/api/auth/jellyfin/quick-connect", tags=["authentication"])

quick_connect_attempts = QuickConnectAttempts()
"""Pending Quick Connect sign-ins of this process, with their start limits."""

quick_connect_offered = RecentAnswer(10)
"""Whether Jellyfin offers Quick Connect, asked at most once every ten seconds."""

_REFUSALS_FOR_CODE = {
    **_REFUSALS,
    _INVALID: (401, "Jellyfin did not accept this Quick Connect code"),
}
_ATTEMPT_SECONDS = 30 * 60


def _handle_cookie() -> str:
    return f"{settings.auth_cookie_name}_quick_connect"


def _handle(request: Request) -> str | None:
    return unique_cookie_value(request, _handle_cookie())


def _set_handle(response: Response, request: Request, handle: str | None) -> None:
    from . import auth_api

    secure, path = auth_api._cookie_scope(request)
    if handle is None:
        response.delete_cookie(
            _handle_cookie(), httponly=True, secure=secure, samesite="strict", path=path
        )
        return
    response.set_cookie(
        _handle_cookie(),
        handle,
        max_age=_ATTEMPT_SECONDS,
        httponly=True,
        secure=secure,
        samesite="strict",
        path=path,
    )


def _attempt_error(status: int, code: str, detail: str) -> Response:
    from . import auth_api

    answer = auth_api._error(status, code, detail)
    auth_api._set_no_store(answer)
    return answer


def _unavailable() -> Response:
    return _attempt_error(503, "jellyfin_unavailable", "Jellyfin could not be reached")


def _no_attempt() -> Response:
    return _attempt_error(404, "quick_connect_not_found", "No Quick Connect sign-in is pending")


@router.get("", response_model=QuickConnectAvailability)
def quick_connect_availability(request: Request, response: Response, db: Session = Depends(get_db)):
    """Whether sign-in offers Quick Connect: Jellyfin is configured, reachable, and allows it.

    One request at a time asks Jellyfin; the answer is reused for ten seconds,
    or three after Jellyfin did not answer.
    """

    from . import auth_api

    if (refused := _setup_required(db)) is not None:
        return refused
    auth_api._set_no_store(response)
    return QuickConnectAvailability(enabled=quick_connect_offered.get(lambda: _offered(db)))


def _offered(db: Session) -> bool | None:
    """Whether Jellyfin allows Quick Connect; None when Jellyfin did not answer."""
    adapter = open_adapter(db)
    if adapter is None:
        return False
    with adapter:
        enabled = quick_connect_enabled(adapter)
    return enabled if isinstance(enabled, bool) else None


@router.post("/initiate", response_model=QuickConnectAttemptResponse, status_code=201)
def quick_connect_initiate(request: Request, response: Response, db: Session = Depends(get_db)):
    """Start a Quick Connect sign-in for this browser, replacing its earlier one."""

    from . import auth_api

    if (refused := _setup_required(db)) is not None:
        return refused
    adapter = open_adapter(db)
    if adapter is None:
        return _not_configured()
    client = auth_api._client_address(request)
    refused = quick_connect_attempts.admit(client.throttle_key)
    if refused:
        adapter.close()
        _record_refused_start(db, request, client.address, first=refused.first)
        throttled = auth_api._error(
            429,
            "quick_connect_throttled",
            "Too many Quick Connect codes were requested",
            retry_after=refused.retry_after,
        )
        auth_api._set_no_store(throttled)
        return throttled
    with adapter:
        started = initiate_quick_connect(adapter)
    if isinstance(started, Unverified):
        if started.reason is UnverifiedReason.CREDENTIALS_REJECTED:
            return _attempt_error(403, "quick_connect_disabled", "Jellyfin Quick Connect is off")
        return _unavailable()
    handle = quick_connect_attempts.begin(started, replacing=_handle(request))
    if handle is None:
        return _attempt_error(
            503,
            "quick_connect_busy",
            "Too many Quick Connect codes are waiting. Try again later or sign in with a password.",
        )
    _set_handle(response, request, handle)
    auth_api._set_no_store(response)
    return QuickConnectAttemptResponse(state="pending", code=started.code)


def _record_refused_start(
    db: Session, request: Request, client_address: str | None, *, first: bool
) -> None:
    """Log every refused start, and store only the first refusal of each lockout."""
    now = auth_policy.session_now()
    if (log := authentication_log(request)) is not None:
        log.emit(
            event="auth.login_throttled",
            outcome="denied",
            severity="warning",
            context=audit_context(request, client_address),
            actor="anonymous",
            now=now,
        )
    if first:
        stored_audit.sign_in_refused(
            db, AuditOutcome.DENIED, "throttled", target=None, now=now, method="quick_connect"
        )


@router.get("/status", response_model=QuickConnectAttemptResponse)
def quick_connect_status(request: Request, response: Response, db: Session = Depends(get_db)):
    """Ask Jellyfin whether this browser's code was approved, and report the attempt's state.

    Jellyfin is asked at most once a second per attempt; between asks the last
    known state is reported. A code Jellyfin no longer knows, or one left when
    Quick Connect was turned off, is expired. An outage leaves the attempt pending and answers 503.
    """

    from . import auth_api

    if (refused := _setup_required(db)) is not None:
        return refused
    handle = _handle(request)
    attempt = quick_connect_attempts.current(handle) if handle else None
    if attempt is None:
        return _no_attempt()
    due = quick_connect_attempts.due(handle)
    if due is not None and due.secret is not None:
        adapter = open_adapter(db)
        if adapter is None:
            return _not_configured()
        with adapter:
            known = quick_connect_request(adapter, due.secret)
        if isinstance(known, Unverified):
            if known.reason is not UnverifiedReason.CREDENTIALS_REJECTED:
                return _unavailable()
            known = None
        attempt = quick_connect_attempts.observe(handle, known)
        if attempt is None:
            return _no_attempt()
    auth_api._set_no_store(response)
    return QuickConnectAttemptResponse(state=attempt.state.value, code=attempt.code)


@router.post("/complete", response_model=AuthenticationStatusResponse)
def quick_connect_complete(
    payload: QuickConnectSignInRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Spend this browser's approved code and sign its Jellyfin user in, as password sign-in does.

    The code is spent before Jellyfin is asked, so it signs in at most once.
    """

    if (refused := _setup_required(db)) is not None:
        return refused
    handle = _handle(request)
    attempt = quick_connect_attempts.current(handle) if handle else None
    if attempt is None:
        return _no_attempt()
    adapter = open_adapter(db)
    if adapter is None:
        return _not_configured()
    secret = quick_connect_attempts.claim(handle)
    if secret is None:
        adapter.close()
        state = quick_connect_attempts.current(handle) or attempt
        return _attempt_error(
            409, f"quick_connect_{state.state.value}", "This Quick Connect code cannot sign in"
        )
    answer = _issue_session(
        db,
        request,
        response,
        adapter,
        lambda: authenticate_quick_connect(adapter, secret),
        remember_me=payload.remember_me,
        return_to=payload.return_to,
        now=auth_policy.session_now(),
        refusals=_REFUSALS_FOR_CODE,
        method="quick_connect",
    )
    _set_handle(answer if isinstance(answer, Response) else response, request, None)
    return answer


@router.post("/cancel", status_code=204)
def quick_connect_cancel(request: Request) -> Response:
    """Cancel this browser's Quick Connect sign-in; its code can no longer sign in here."""

    from . import auth_api

    handle = _handle(request)
    if handle:
        quick_connect_attempts.cancel(handle)
    answer = Response(status_code=204)
    _set_handle(answer, request, None)
    auth_api._set_no_store(answer)
    return answer
