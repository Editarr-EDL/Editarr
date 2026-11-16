"""Each user's API key: status, issue (create or rotate), reveal, and revoke.

`/api/auth/api-key` acts on the caller's own key. `/api/users/{user_id}/api-key`
names the holder: the owner may act on any key, and an admin may revoke an
ordinary user's. Issue and reveal need a browser session and the password in
the same request (`reauthentication`); the access middleware already refuses
API keys on routes that consist only of such actions. Responses are no-store.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.persistence.busy import is_busy
from scanner.security import auth_policy
from scanner.security.access.api_shapes import Reauthentication
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.fresh import FreshAuthentication
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import access_denied_response, request_access
from scanner.security.access.targets import AccountRef, ActionPair, resolve_action
from scanner.security.api_key_crypto import ApiKeyWrapKey, load_api_key_wrap_key
from scanner.security.api_keys import revoke_api_key, rotate_api_key, stored_api_key
from scanner.security.audit import authentication as stored_audit
from scanner.security.audit.actions import AuditAction

from . import account_operations as operations
from .reauthentication import reauthentication_guard
from .users_access import Caller, Details

router = APIRouter(prefix="/api/users", tags=["auth"])


class ApiKeyStatus(BaseModel):
    configured: bool
    created_at: datetime | None = None
    revealable: bool = False


class ApiKeySecret(BaseModel):
    api_key: str
    created_at: datetime


class FreshKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reauthentication: Reauthentication | None = None


class _Refusal(Exception):
    def __init__(self, status: int, code: str, detail: str, retry: bool = False) -> None:
        self.status, self.code, self.detail, self.retry = status, code, detail, retry


@dataclass(frozen=True)
class _Caller(Caller):
    wrap_key: ApiKeyWrapKey


def _caller(
    request: Request,
    response: Response,
    access: PrincipalAccess = Depends(request_access),
    db: Session = Depends(get_db),
) -> _Caller:
    from . import auth_api

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return _Caller(
        db,
        access,
        auth_api._client_address(request).throttle_key,
        auth_policy.session_now(),
        load_api_key_wrap_key(auth_api.settings.data_dir),
    )


def _authorize(
    caller: _Caller,
    audited: AuditAction,
    action: C | ActionPair,
    target: AccountRef,
    proof: Reauthentication | None = None,
    *,
    details: Details | None = None,
) -> FreshAuthentication | None:
    """Decide through the fresh-authentication guard and store any refusal."""

    try:
        return operations.Proofs(caller, proof, guard=reauthentication_guard).decide(action, target)
    except AccessDenied as denial:
        caller.refuse(
            audited,
            target.user_id,
            denial.code.value,
            capability=resolve_action(action, caller.access.principal.user_id, target),
            details=details,
        )
        raise


def _refuse(caller: _Caller, audited: AuditAction, target: AccountRef, reason: str) -> None:
    principal = caller.access.principal
    stored_audit.api_key_refused(
        caller.db,
        audited,
        principal.user_id,
        principal.credential,
        target=target.user_id,
        reason=reason,
        now=caller.now,
    )


def _issue(caller: _Caller, target: AccountRef, body: FreshKeyRequest) -> ApiKeySecret:
    had_key = stored_api_key(caller.db, target.user_id, caller.wrap_key) is not None
    own = C.API_KEYS_ROTATE_OWN if had_key else C.API_KEYS_CREATE_OWN
    pair = ActionPair(own, C.API_KEYS_ISSUE_OTHER)
    variant = {"replaced": had_key}
    fresh = _authorize(
        caller, AuditAction.API_KEY_ISSUE, pair, target, body.reauthentication, details=variant
    )
    with operations.locked(
        caller, pair, target, AuditAction.API_KEY_ISSUE, fresh=fresh, details=variant
    ) as target:
        had_key = stored_api_key(caller.db, target.user_id, caller.wrap_key) is not None
        variant["replaced"] = had_key
        own = C.API_KEYS_ROTATE_OWN if had_key else C.API_KEYS_CREATE_OWN
        require(caller.access, ActionPair(own, C.API_KEYS_ISSUE_OTHER), target, fresh=fresh)
        principal = caller.access.principal
        key = rotate_api_key(
            caller.db, target.user_id, caller.wrap_key, now=caller.now, commit=False
        )
        stored_audit.api_key_changed(
            caller.db,
            principal.user_id,
            principal.credential,
            target=target.user_id,
            issued=True,
            had_key=had_key,
            now=caller.now,
        )
        caller.db.commit()
    return ApiKeySecret(api_key=key, created_at=caller.now)


def _reveal(caller: _Caller, target: AccountRef, body: FreshKeyRequest) -> ApiKeySecret:
    pair = ActionPair(C.API_KEYS_REVEAL_OWN, C.API_KEYS_REVEAL_OTHER)
    fresh = _authorize(caller, AuditAction.API_KEY_REVEAL, pair, target, body.reauthentication)
    with operations.locked(caller, pair, target, AuditAction.API_KEY_REVEAL, fresh=fresh) as target:
        stored = stored_api_key(caller.db, target.user_id, caller.wrap_key)
        if stored is None or stored.value is None:
            missing = stored is None
            code = "api_key_not_configured" if missing else "api_key_unrecoverable"
            _refuse(caller, AuditAction.API_KEY_REVEAL, target, code)
            raise _Refusal(
                404 if missing else 409,
                code,
                "No API key is configured"
                if missing
                else "This API key cannot be shown; generate a new one",
            )
        principal = caller.access.principal
        stored_audit.api_key_revealed(
            caller.db,
            principal.user_id,
            principal.credential,
            target=target.user_id,
            now=caller.now,
        )
        caller.db.commit()
    return ApiKeySecret(api_key=stored.value, created_at=stored.created_at)


def _revoke(caller: _Caller, target: AccountRef) -> ApiKeyStatus:
    pair = ActionPair(C.API_KEYS_REVOKE_OWN, C.API_KEYS_REVOKE_OTHER)
    fresh = _authorize(caller, AuditAction.API_KEY_REVOKE, pair, target)
    with operations.locked(caller, pair, target, AuditAction.API_KEY_REVOKE, fresh=fresh) as target:
        principal = caller.access.principal
        had_key = revoke_api_key(caller.db, target.user_id, commit=False)
        stored_audit.api_key_changed(
            caller.db,
            principal.user_id,
            principal.credential,
            target=target.user_id,
            issued=False,
            had_key=had_key,
            now=caller.now,
        )
        caller.db.commit()
    return ApiKeyStatus(configured=False)


def _respond(caller: _Caller, operation: Callable[[], BaseModel]) -> BaseModel | JSONResponse:
    """Run one key operation, rendering refusals in the `{"code", "detail"}` envelope.

    A busy database answers a retryable 503 and changes nothing.
    """

    try:
        return operation()
    except OperationalError as error:
        caller.db.rollback()
        if not is_busy(error):
            raise
        refusal = _Refusal(503, "database_busy", "The database is busy; retry shortly", True)
    except AccessDenied as denial:
        return access_denied_response(denial)
    except _Refusal as raised:
        refusal = raised
    headers = {"Cache-Control": "no-store"} | ({"Retry-After": "1"} if refusal.retry else {})
    return JSONResponse(
        status_code=refusal.status,
        content={"code": refusal.code, "detail": refusal.detail},
        headers=headers,
    )


def _status(caller: _Caller) -> ApiKeyStatus:
    require(caller.access, C.PROFILE_VIEW_OWN, caller.own)
    stored = stored_api_key(caller.db, caller.own.user_id, caller.wrap_key)
    if stored is None:
        return ApiKeyStatus(configured=False)
    return ApiKeyStatus(
        configured=True, created_at=stored.created_at, revealable=stored.value is not None
    )


def own_status(caller: _Caller = Depends(_caller)):
    """The caller's key metadata; the value itself is only returned by reveal."""

    return _respond(caller, lambda: _status(caller))


def own_issue(body: FreshKeyRequest, caller: _Caller = Depends(_caller)):
    """Create or replace the caller's key; the previous key stops working immediately."""

    return _respond(caller, lambda: _issue(caller, caller.own, body))


def own_reveal(body: FreshKeyRequest, caller: _Caller = Depends(_caller)):
    """Show the caller's current key after password confirmation."""

    return _respond(caller, lambda: _reveal(caller, caller.own, body))


def own_revoke(caller: _Caller = Depends(_caller)):
    """Disable the caller's key without affecting browser sessions."""

    return _respond(caller, lambda: _revoke(caller, caller.own))


@router.post("/{user_id}/api-key", response_model=ApiKeySecret)
def user_issue(user_id: int, body: FreshKeyRequest, caller: _Caller = Depends(_caller)):
    """Create or replace another account's key (owner only)."""

    return _respond(
        caller, lambda: _issue(caller, caller.account(user_id, C.API_KEYS_ISSUE_OTHER), body)
    )


@router.post("/{user_id}/api-key/reveal", response_model=ApiKeySecret)
def user_reveal(user_id: int, body: FreshKeyRequest, caller: _Caller = Depends(_caller)):
    """Show another account's key (owner only)."""

    return _respond(
        caller, lambda: _reveal(caller, caller.account(user_id, C.API_KEYS_REVEAL_OTHER), body)
    )


@router.delete("/{user_id}/api-key", response_model=ApiKeyStatus)
def user_revoke(user_id: int, caller: _Caller = Depends(_caller)):
    """Revoke another account's key: the owner any, an admin an ordinary user's."""

    return _respond(
        caller, lambda: _revoke(caller, caller.account(user_id, C.API_KEYS_REVOKE_OTHER))
    )


def register_api_key_routes(own: APIRouter) -> None:
    """Register the caller's own key routes under the authentication router's prefix."""

    own.add_api_route("/api-key", own_status, methods=["GET"], response_model=ApiKeyStatus)
    own.add_api_route("/api-key", own_issue, methods=["POST"], response_model=ApiKeySecret)
    own.add_api_route("/api-key/reveal", own_reveal, methods=["POST"], response_model=ApiKeySecret)
    own.add_api_route("/api-key", own_revoke, methods=["DELETE"], response_model=ApiKeyStatus)
