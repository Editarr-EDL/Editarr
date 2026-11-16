"""Managing other accounts: status, sessions, roles, and bulk requests.

Each route acts on another account through `account_operations`, so a single
request and every target of a bulk request pass the same capability,
target-account protection, and fresh-authentication checks. Role changes need
the caller's Jellyfin password in the request (`reauthentication`).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scanner.security.access.api_shapes import Reauthentication
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.identity import AccountStatus
from scanner.security.access.targets import AccountRef
from scanner.security.access.vocabulary import Role
from scanner.security.media_access.management import ScopeChoice, UnsupportedMediaAccessChange

from . import account_operations as operations
from .media_access_api import ScopeBody, scope_choice
from .users_access import Caller, Refusal, caller, respond
from .users_api import read_detail
from .users_api_models import AccountDetail, SessionsRevoked, StatusChange

router = APIRouter(prefix="/api/users", tags=["users"])


class RoleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "user"]
    reauthentication: Reauthentication | None = None


class BulkRequest(BaseModel):
    """One operation for up to 100 accounts; `role` or `media_access` as the action needs."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["disable", "enable", "revoke_sessions", "set_role", "set_media_access"]
    user_ids: list[int] = Field(min_length=1, max_length=100)
    role: Literal["admin", "user"] | None = None
    media_access: ScopeBody | None = Field(
        default=None, description="The selection to store; `inherit: true` returns to the default"
    )
    reauthentication: Reauthentication | None = None

    @model_validator(mode="after")
    def parameters(self):
        if len(set(self.user_ids)) != len(self.user_ids):
            raise ValueError("Each account may appear once")
        if (self.action == "set_role") != (self.role is not None):
            raise ValueError("role is required for set_role and only for it")
        if (self.action == "set_media_access") != (self.media_access is not None):
            raise ValueError("media_access is required for set_media_access and only for it")
        return self


class BulkResult(BaseModel):
    user_id: int
    outcome: Literal["applied", "refused"]
    code: str | None = None


class BulkResponse(BaseModel):
    results: list[BulkResult]


def _target(caller: Caller, user_id: int, operation: operations.Operation) -> AccountRef:
    return caller.other(user_id, operation.capability, audited=operation.audited)


def _single(caller: Caller, user_id: int, operation, proof=None) -> None:
    target = _target(caller, user_id, operation)
    operations.run(caller, operation, target, operations.Proofs(caller, proof))


@router.put("/{user_id}/status", response_model=AccountDetail)
def change_status(user_id: int, body: StatusChange, caller: Caller = Depends(caller)):
    """Disable or re-enable another account. Disabling ends its sessions and keeps its data.

    An account without a Jellyfin link stays disabled (`account_link_required`).
    """

    def change() -> AccountDetail:
        _single(caller, user_id, operations.status(body.status))
        return read_detail(caller, user_id)

    return respond(caller, change)


@router.delete("/{user_id}/sessions", response_model=SessionsRevoked)
def revoke_sessions(user_id: int, caller: Caller = Depends(caller)):
    """End every browser session of another account; its API key keeps working."""

    def revoke() -> SessionsRevoked:
        target = _target(caller, user_id, operations.REVOKE_SESSIONS)
        details = operations.run(
            caller, operations.REVOKE_SESSIONS, target, operations.Proofs(caller, None)
        )
        return SessionsRevoked(sessions_revoked=details["sessions_revoked"])

    return respond(caller, revoke)


@router.put("/{user_id}/role", response_model=AccountDetail)
def assign_role(user_id: int, body: RoleChange, caller: Caller = Depends(caller)):
    """Make another account an admin or an ordinary user (owner only)."""

    def assign() -> AccountDetail:
        _single(caller, user_id, operations.role(Role(body.role)), body.reauthentication)
        return read_detail(caller, user_id)

    return respond(caller, assign)


def _bulk_operation(body: BulkRequest) -> operations.Operation:
    if body.action == "set_role":
        return operations.role(Role(body.role))
    if body.action == "set_media_access":
        choice: ScopeChoice | None = scope_choice(body.media_access)
        return operations.media_scope(choice)
    if body.action == "revoke_sessions":
        return operations.REVOKE_SESSIONS
    return operations.status(
        AccountStatus.ACTIVE if body.action == "enable" else AccountStatus.DISABLED
    )


@router.post("/bulk", response_model=BulkResponse)
def bulk(body: BulkRequest, caller: Caller = Depends(caller)):
    """Apply one operation to each listed account, checking every target on its own.

    Targets are processed in order and each change commits alone. A wrong or
    throttled password stops the request with that refusal; changes already
    applied stay applied.
    """

    def apply() -> BulkResponse:
        operation = _bulk_operation(body)
        proofs = operations.Proofs(caller, body.reauthentication)
        results = []
        for user_id in body.user_ids:
            try:
                target = _target(caller, user_id, operation)
                operations.run(caller, operation, target, proofs)
            except AccessDenied as denial:
                if denial.code.value in {
                    "reauthentication_failed",
                    "reauthentication_throttled",
                    "jellyfin_unavailable",
                }:
                    raise
                code = denial.code.value
            except Refusal as refusal:
                code = refusal.code
            except UnsupportedMediaAccessChange as unsupported:
                code = unsupported.code
            else:
                results.append(BulkResult(user_id=user_id, outcome="applied"))
                continue
            caller.db.rollback()
            results.append(BulkResult(user_id=user_id, outcome="refused", code=code))
        return BulkResponse(results=results)

    return respond(caller, apply)
