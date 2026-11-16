"""First-run setup: a Jellyfin administrator becomes the owner and Editarr mints its key.

Shares the sign-in cookie, CSRF, and session policy of `auth_api`. Setup is
open to anyone who reaches Editarr before an owner exists and makes Editarr
contact the address it is given, so every refusal counts toward the client
address's sign-in limit. Nothing counts toward the typed name: the caller picks
the server that judges the password, so a name limit would let anyone lock the
real administrator out.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.integrations.jellyfin_setup import JellyfinSetupClient, SetupRefusal, SetupRefused
from scanner.security import auth_policy
from scanner.security.audit import authentication as stored_audit
from scanner.security.auth_repository import issue_session
from scanner.security.jellyfin_accounts.owner_setup import set_up_owner

from .auth_api_models import AuthenticationStatusResponse, SetupRequest
from .auth_rate_limit import LoginRateLimiter
from .auth_status import signed_in

setup_rate_limiter = LoginRateLimiter()
"""Setup refusals per client address."""

setup_transport: httpx.BaseTransport | None = None
"""The transport setup reaches Jellyfin through; tests set a fake, the app keeps None."""

_SETUP_REFUSALS = {
    SetupRefusal.BAD_URL: (400, "Enter a valid http or https Jellyfin server address"),
    SetupRefusal.UNREACHABLE: (502, "Editarr could not reach a Jellyfin server at that address"),
    SetupRefusal.NOT_JELLYFIN: (502, "That address did not answer as a Jellyfin server"),
    SetupRefusal.INVALID_CREDENTIALS: (401, "Jellyfin did not accept that username and password"),
    SetupRefusal.NOT_ADMINISTRATOR: (
        403,
        "Only a Jellyfin administrator can set up Editarr",
    ),
    SetupRefusal.KEY_REFUSED: (502, "Jellyfin did not create an API key for Editarr"),
    SetupRefusal.KEY_LEFT_BEHIND: (
        502,
        "Setup stopped, and Jellyfin may still hold an API key named Editarr from this attempt. "
        "Delete it in Jellyfin under Dashboard > API Keys, then try again",
    ),
    SetupRefusal.ALREADY_COMPLETE: (409, "Administrator setup is already complete"),
}
"""HTTP status and message for each way setup stores nothing."""


def auth_setup(
    payload: SetupRequest, request: Request, response: Response, db: Session = Depends(get_db)
):
    """Make a Jellyfin administrator the owner, store Editarr's minted key, and sign in."""

    from . import auth_api

    now = auth_policy.session_now()
    address = auth_api._client_address(request).throttle_key
    retry_after = setup_rate_limiter.address_retry_after(address)
    if retry_after:
        throttled = auth_api._error(429, "login_throttled", "Too many setup attempts; retry later")
        throttled.headers["Retry-After"] = str(retry_after)
        auth_api._set_no_store(throttled)
        return throttled

    def issue(user_id: int):
        session = issue_session(
            db,
            remember_me=payload.remember_me,
            user_id=user_id,
            now=now,
            configuration=auth_api.settings,
            commit=False,
        )
        return user_id, session

    try:
        with JellyfinSetupClient(payload.server_url, transport=setup_transport) as client:
            user_id, session = set_up_owner(
                db,
                client,
                payload.username,
                payload.password.get_secret_value(),
                clock=auth_policy.session_now,
                issue=issue,
            )
    except SetupRefused as refused:
        db.rollback()
        if refused.refusal is SetupRefusal.ALREADY_COMPLETE:
            stored_audit.setup_failed(db, now=now)
        else:
            setup_rate_limiter.record_address_failure(address)
        status, detail = _SETUP_REFUSALS[refused.refusal]
        refusal = auth_api._error(status, refused.refusal.value, detail)
        auth_api._set_no_store(refusal)
        return refusal
    auth_api._set_session_cookie(
        response, session.token, remember_me=session.remember_me, request=request
    )
    auth_api._set_no_store(response)
    activate_queue_processing = getattr(request.app.state, "activate_scan_queue_processing", None)
    if activate_queue_processing is not None:
        activate_queue_processing()
    return signed_in(
        db,
        request,
        user_id=user_id,
        session_id=session.id,
        csrf_token=session.csrf_token,
        return_to=payload.return_to,
    )


def register_setup_routes(own: APIRouter) -> None:
    """Register setup under the authentication router's prefix."""

    own.add_api_route(
        "/setup",
        auth_setup,
        methods=["POST"],
        response_model=AuthenticationStatusResponse,
        status_code=201,
    )
