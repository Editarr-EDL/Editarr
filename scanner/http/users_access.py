"""Shared request plumbing for the Users, permissions, and media-access routes.

A `Caller` carries the live principal and resolves account targets. Mutations
authorize through MU-13's fresh-authentication guard, store each refusal as an
audit row, and stage their success event in the change's own transaction.
`respond` renders every refusal in the `{"code", "detail"}` envelope, no-store.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.persistence.busy import is_busy
from scanner.security import auth_policy
from scanner.security.access.api_shapes import Reauthentication
from scanner.security.access.authorization import HiddenAccount, resolve_account
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import UserId
from scanner.security.access.overrides import OverrideRejected
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.access.request_access import access_denied_response, request_access
from scanner.security.access.targets import AccountRef, Action, Target, resolve_action
from scanner.security.account_avatars import AvatarRejected
from scanner.security.account_profiles import ProfileValueRejected
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import record_event, stage_event
from scanner.security.audit.variants import operation_variant
from scanner.security.media_access.management import UnsupportedMediaAccessChange

from .reauthentication import reauthentication_guard

Details = Mapping[str, str | int | bool]


class Refusal(Exception):
    """A refusal this module renders as `{"code", "detail"}` with `status`."""

    def __init__(self, status: int, code: str, detail: str, retry: bool = False) -> None:
        super().__init__(code)
        self.status, self.code, self.detail, self.retry = status, code, detail, retry


@dataclass(frozen=True)
class Caller:
    db: Session
    access: PrincipalAccess
    address: str
    now: datetime

    @property
    def own(self) -> AccountRef:
        return AccountRef(self.access.principal.user_id, self.access.principal.account.role)

    def account(
        self, user_id: int, *actions: Action, audited: AuditAction | None = None
    ) -> AccountRef:
        """The named account, or `not_found` for one the caller may not learn exists.

        `actions` are what the route may decide on the account; see `resolve_account`.
        A mutation names its `audited` action, which records the refusal of an
        existing hidden account as the decision itself would have.
        """

        try:
            return resolve_account(self.db, self.access, user_id, *actions)
        except HiddenAccount as hidden:
            if audited is not None and hidden.account is not None:
                capability = (
                    resolve_action(actions[0], self.access.principal.user_id, hidden.account)
                    if len(actions) == 1
                    else None
                )
                self.refuse(
                    audited, hidden.account.user_id, hidden.code.value, capability=capability
                )
            raise

    def other(
        self, user_id: int, *actions: Action, audited: AuditAction | None = None
    ) -> AccountRef:
        """An account other than the caller's, for actions that only manage others."""

        target = self.account(user_id, *actions, audited=audited)
        if target.user_id == self.own.user_id:
            raise Refusal(409, "own_account", "Change your own account from your profile")
        return target

    def authorize(
        self,
        audited: AuditAction,
        action: Action,
        target: Target,
        proof: Reauthentication | None = None,
        *,
        target_user: int | None = None,
    ) -> Capability:
        """Decide through the fresh-authentication guard, storing any refusal."""

        try:
            return reauthentication_guard.authorize(
                self.db, self.access, action, target, proof, address=self.address
            )
        except AccessDenied as denial:
            owner = target.user_id if isinstance(target, AccountRef) else target_user
            self.refuse(
                audited,
                owner,
                denial.code.value,
                capability=resolve_action(action, self.access.principal.user_id, target),
            )
            raise

    def event(
        self,
        action: AuditAction,
        target: int | None,
        details: Details,
        outcome: AuditOutcome = AuditOutcome.SUCCEEDED,
    ) -> AuditEvent:
        principal = self.access.principal
        return AuditEvent(
            self.now,
            principal.user_id,
            principal.credential,
            action,
            outcome,
            target_user=UserId(target) if target is not None else None,
            details=details,
        )

    def stage(self, action: AuditAction, target: int | None, details: Details) -> None:
        """Add the success event to the open transaction; the caller commits both."""

        stage_event(self.db, self.event(action, target, details))

    def refuse(
        self,
        action: AuditAction,
        target: int | None,
        reason: str,
        *,
        capability: Capability | None = None,
        details: Details | None = None,
    ) -> None:
        """Store a refusal in its own transaction; a wrong password counts as failed."""

        self.db.rollback()
        outcome = (
            AuditOutcome.FAILED if reason == "reauthentication_failed" else AuditOutcome.DENIED
        )
        variant = operation_variant(action, capability) | dict(details or {})
        record_event(self.db, self.event(action, target, variant | {"reason": reason}, outcome))


def caller(
    request: Request,
    response: Response,
    access: PrincipalAccess = Depends(request_access),
    db: Session = Depends(get_db),
) -> Caller:
    from . import auth_api

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return Caller(
        db, access, auth_api._client_address(request).throttle_key, auth_policy.session_now()
    )


def _refusal(error: Exception) -> Refusal | None:
    if isinstance(error, Refusal):
        return error
    if isinstance(error, ProfileValueRejected | OverrideRejected):
        return Refusal(422, error.code, str(error))
    if isinstance(error, AvatarRejected):
        return Refusal(413 if error.code == "avatar_too_large" else 422, error.code, str(error))
    if isinstance(error, UnsupportedMediaAccessChange):
        return Refusal(409, error.code, "That change does not apply to this account")
    return None


def respond(caller: Caller, operation: Callable[[], BaseModel | Response]):
    """Run one operation and render its refusal; a busy database answers a retryable 503."""

    try:
        return operation()
    except OperationalError as error:
        caller.db.rollback()
        if not is_busy(error):
            raise
        refusal = Refusal(503, "database_busy", "The database is busy; retry shortly", True)
    except Exception as error:
        if isinstance(error, AccessDenied):
            caller.db.rollback()
            return access_denied_response(error)
        refusal = _refusal(error)
        if refusal is None:
            raise
        caller.db.rollback()
    headers = {"Cache-Control": "no-store"} | ({"Retry-After": "1"} if refusal.retry else {})
    return JSONResponse(
        status_code=refusal.status,
        content={"code": refusal.code, "detail": refusal.detail},
        headers=headers,
    )
