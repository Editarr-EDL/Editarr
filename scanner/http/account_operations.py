"""Account management operations shared by single-account and bulk routes.

An `Operation` names the capability it exercises on another account, its
audit action, and how it changes one target. `run` decides the capability for
that target, proving the password at most once per request through `Proofs`,
stores a refusal, or applies the change and commits it with its event. The
change holds the write lock while revalidating the caller's authority and the
target's current protection, so a stale request cannot apply the change.
Bulk requests run the same operation per target, so a protected account in a
batch is refused exactly as it would be alone.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypeVar, cast

from scanner.persistence.write_lock import write_lock
from scanner.security import account_permissions as permissions
from scanner.security import account_repository as accounts
from scanner.security.access.api_shapes import AccessErrorCode, Reauthentication
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.fresh import FreshAuthentication
from scanner.security.access.identity import AccountStatus, DisableCause
from scanner.security.access.targets import AccountRef, Action, Target, resolve_action
from scanner.security.access.vocabulary import Role
from scanner.security.access.write_authorization import revalidate
from scanner.security.audit.actions import AuditAction
from scanner.security.auth_repository_sessions import revoke_user_sessions
from scanner.security.media_access import management

from .reauthentication import ReauthenticationGuard, reauthentication_guard
from .users_access import Caller, Details, Refusal

Apply = Callable[[Caller, AccountRef, FreshAuthentication | None], Details]
_Target = TypeVar("_Target", bound=Target)


@dataclass(frozen=True)
class Operation:
    capability: C
    audited: AuditAction
    apply: Apply
    """Stage the change and its event's details; `run` stages the event and commits."""

    staged: bool = False
    """True when `apply` stages its own event because the change commits itself."""

    prepare: Callable[[], object] = lambda: None
    """Slow work for an authorized change, done before `run` takes the write lock."""


class Proofs:
    """The request's one optional password, verified at most once and only when needed."""

    def __init__(
        self,
        caller: Caller,
        proof: Reauthentication | None,
        *,
        guard: ReauthenticationGuard | None = None,
    ) -> None:
        self.caller, self.proof = caller, proof
        self.guard = guard or reauthentication_guard
        self.fresh: FreshAuthentication | None = None

    def decide(self, capability: Action, target: Target) -> FreshAuthentication | None:
        """Return the proof the capability needs (or None), or raise `AccessDenied`."""

        access = self.caller.access
        decision = decide(access, capability, target)
        if isinstance(decision, Allowed):
            return None
        if decision.code is not AccessErrorCode.REAUTHENTICATION_REQUIRED or self.proof is None:
            raise AccessDenied(decision.code)
        if self.fresh is None:
            self.fresh = self.guard.prove(
                self.caller.db, access, self.proof, address=self.caller.address
            )
        require(access, capability, target, fresh=self.fresh)
        return self.fresh


def authorized(
    caller: Caller,
    proofs: Proofs,
    capability: C,
    target: Target,
    audited: AuditAction,
) -> FreshAuthentication | None:
    """Decide one capability for one target, storing a refusal before raising it."""

    try:
        return proofs.decide(capability, target)
    except AccessDenied as denial:
        owner = target.user_id if isinstance(target, AccountRef) else None
        caller.refuse(audited, owner, denial.code.value, capability=capability)
        raise


def run(caller: Caller, operation: Operation, target: AccountRef, proofs: Proofs) -> Details:
    """Apply one operation to one other account and commit it with its audit event."""

    fresh = authorized(caller, proofs, operation.capability, target, operation.audited)
    operation.prepare()
    stale_code = (
        AccessErrorCode.PERMISSION_REVISION_STALE
        if operation.capability is C.OWNERSHIP_TRANSFER
        else AccessErrorCode.CAPABILITY_DENIED
    )
    with locked(
        caller, operation.capability, target, operation.audited, fresh=fresh, stale_code=stale_code
    ) as current:
        details = operation.apply(caller, current, fresh)
        if not operation.staged:
            caller.stage(operation.audited, current.user_id, details)
        caller.db.commit()
    return details


@contextmanager
def locked(
    caller: Caller,
    action: Action,
    target: _Target,
    audited: AuditAction,
    *,
    fresh: FreshAuthentication | None = None,
    stale_code: AccessErrorCode = AccessErrorCode.CAPABILITY_DENIED,
    details: Details | None = None,
) -> Iterator[_Target]:
    """Hold authorization and the operation in one write transaction; audit refusals."""

    try:
        with write_lock(caller.db):
            current = revalidate(
                caller.db, caller.access, action, target, fresh=fresh, stale_code=stale_code
            )
            yield cast(_Target, current)
    except AccessDenied as denial:
        owner = target.user_id if isinstance(target, AccountRef) else None
        caller.refuse(
            audited,
            owner,
            denial.code.value,
            capability=resolve_action(action, caller.access.principal.user_id, target),
            details=details,
        )
        raise


def status(value: AccountStatus) -> Operation:
    capability = (
        C.ACCOUNTS_ENABLE_OTHER if value is AccountStatus.ACTIVE else C.ACCOUNTS_DISABLE_OTHER
    )

    def apply(caller: Caller, target: AccountRef, _fresh) -> Details:
        cause = None if value is AccountStatus.ACTIVE else DisableCause.LOCAL
        if cause is None and not accounts.is_linked(caller.db, target.user_id):
            raise Refusal(409, "account_link_required", "Link this account to Jellyfin first")
        ended = accounts.set_disable_cause(caller.db, target.user_id, cause, now=caller.now)
        return {"status": value.value, "sessions_revoked": ended}

    return Operation(capability, AuditAction.ACCOUNT_STATUS, apply)


def _revoke(caller: Caller, target: AccountRef, _fresh) -> Details:
    ended = revoke_user_sessions(caller.db, target.user_id, now=caller.now, commit=False)
    return {"sessions_revoked": ended}


REVOKE_SESSIONS = Operation(C.ACCOUNTS_SESSIONS_REVOKE_OTHER, AuditAction.SESSIONS_REVOKE, _revoke)


def role(value: Role) -> Operation:
    def apply(caller: Caller, target: AccountRef, _fresh) -> Details:
        permissions.assign_role(caller.db, target.user_id, value, now=caller.now)
        return {"change": "role", "role": value.value}

    return Operation(C.ROLES_ASSIGN, AuditAction.PERMISSIONS_CHANGE, apply)


def media_scope(choice: management.ScopeChoice | None) -> Operation:
    """Set or clear a local account's own media selection; the change commits itself."""

    def apply(caller: Caller, target: AccountRef, fresh) -> Details:
        details = {"change": "user_scope"}
        caller.stage(AuditAction.MEDIA_ACCESS_CHANGE, target.user_id, details)
        management.set_user_scope(
            caller.db, caller.access, target.user_id, choice, fresh=fresh, now=caller.now
        )
        return details

    return Operation(C.MEDIA_ACCESS_USER_EDIT, AuditAction.MEDIA_ACCESS_CHANGE, apply, staged=True)
