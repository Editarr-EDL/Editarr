"""Per-user filter preferences, modes, the starting mode for new accounts, and enforced policy.

`/api/settings/filters/personal` acts on the caller. `/api/settings/filters/users/{id}`
names the account: the owner may edit anyone's values (`filters.preferences.edit.other`),
admins assign ordinary users' modes, and only the owner enforces policy, with the
password in the same request. Refusals use the `{"code", "detail"}` envelope.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from scanner.persistence.repository import filter_enforcement, filter_preferences
from scanner.persistence.repository.filter_preferences import FilterPreferenceConflict
from scanner.security.access.api_shapes import AccessErrorCode, Reauthentication
from scanner.security.access.authorization import (
    HiddenAccount,
    may_attempt,
    require,
)
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.fresh import FreshAuthentication
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.access.targets import AccountRef, ActionPair, TargetMismatch
from scanner.security.access.vocabulary import Role
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import record_event, stage_event
from scanner.settings.filter_policy.effective import FilterPreferencesView
from scanner.settings.filter_policy.events import FilterPolicyChange
from scanner.settings.filter_policy.updates import (
    DefaultMode,
    DefaultModeUpdate,
    FilterModeUpdate,
    FilterPolicyUpdate,
    PreferencesUpdate,
)

from . import account_operations as operations
from .reauthentication import reauthentication_guard
from .users_access import Caller as _Caller
from .users_access import caller as _caller

router = APIRouter(prefix="/api/settings/filters", tags=["settings"])


_VALUES = ActionPair(C.FILTERS_PREFERENCES_EDIT_OWN, C.FILTERS_PREFERENCES_EDIT_OTHER)
_MODE = ActionPair(C.FILTERS_MODE_CHOOSE_OWN, C.FILTERS_MODE_ASSIGN_OTHER)
_VIEWERS = (_VALUES, _MODE, C.FILTERS_POLICY_ENFORCE)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _respond(operation: Callable[[], BaseModel]) -> BaseModel | JSONResponse:
    try:
        return operation()
    except FilterPreferenceConflict as refused:
        return JSONResponse(
            status_code=refused.status,
            content={"code": refused.code, "detail": refused.detail},
            headers={"Cache-Control": "no-store"},
        )


def _view_other(caller: _Caller, target: AccountRef) -> FilterPreferencesView:
    """Allow anyone who may manage the account's values, mode, or policy; else the first denial."""
    denials = []
    for action in _VIEWERS:
        try:
            if may_attempt(caller.access, action, target):
                return filter_preferences.filter_preferences_view(caller.db, target.user_id)
            require(caller.access, action, target)
        except AccessDenied as denial:
            denials.append(denial)
        except TargetMismatch:
            continue
    raise denials[0] if denials else AccessDenied(AccessErrorCode.CAPABILITY_DENIED)


def _save(caller: _Caller, target: AccountRef, change: PreferencesUpdate):
    require(caller.access, _VALUES, target)
    with operations.locked(caller, _VALUES, target, AuditAction.FILTER_POLICY_CHANGE) as current:
        return filter_preferences.save_preferences(
            caller.db, current.user_id, change, now=caller.now
        )


def _mode(caller: _Caller, target: AccountRef, change: FilterModeUpdate):
    require(caller.access, _MODE, target)
    with operations.locked(caller, _MODE, target, AuditAction.FILTER_POLICY_CHANGE) as current:
        return filter_preferences.set_mode(
            caller.db,
            current.user_id,
            change,
            self_service=current.user_id == caller.own.user_id,
            now=caller.now,
        )


def _policy_event(
    access: PrincipalAccess, now: datetime, target_user: int, outcome: AuditOutcome, **details
) -> AuditEvent:
    principal = access.principal
    return AuditEvent(
        now,
        principal.user_id,
        principal.credential,
        AuditAction.FILTER_POLICY_CHANGE,
        outcome,
        target_user=target_user,
        details=details,
    )


def _refused(db: Session, event: AuditEvent, denial: AccessDenied) -> None:
    """Roll back the refused change and record why, in its own transaction."""
    db.rollback()
    failed = denial.code is AccessErrorCode.REAUTHENTICATION_FAILED
    outcome = AuditOutcome.FAILED if failed else AuditOutcome.DENIED
    details = {**event.details, "reason": denial.code.value}
    record_event(db, replace(event, outcome=outcome, details=details))


def _reauthenticate(
    caller: _Caller, target: AccountRef, proof: Reauthentication | None
) -> FreshAuthentication | None:
    return operations.Proofs(caller, proof, guard=reauthentication_guard).decide(
        C.FILTERS_POLICY_ENFORCE,
        target,
    )


def admit_instance_policy_changes(
    db: Session,
    access: PrincipalAccess,
    request: Request,
    proof: Reauthentication | None,
    changes: list[FilterPolicyChange],
) -> None:
    """Admit an uncommitted instance edit that changes users' enforced policies.

    Changing what is enforced on an account needs the same fresh interactive
    authentication as enforcing it directly, so each changed account is authorized
    as `filters.policy.enforce`. A refusal rolls the edit back, is audited, and is
    raised; otherwise one `filters.policy.change` event per account is staged.
    """
    from .auth_api import _client_address

    now = utc_now()
    caller = _Caller(db, access, _client_address(request).throttle_key, now)
    for change in changes:
        cause = change.cause.value
        try:
            target = caller.account(change.user_id, C.FILTERS_POLICY_ENFORCE)
            if target.role is Role.OWNER:
                raise FilterPreferenceConflict(
                    "owner_filter_policy", "The owner's filter policy cannot be enforced"
                )
            _reauthenticate(caller, target, proof)
        except AccessDenied as denial:
            event = _policy_event(
                caller.access, now, change.user_id, AuditOutcome.DENIED, cause=cause
            )
            _refused(caller.db, event, denial)
            raise
        event = _policy_event(
            caller.access,
            now,
            change.user_id,
            AuditOutcome.SUCCEEDED,
            cause=cause,
            policy_revision=change.policy_revision,
        )
        stage_event(caller.db, event)


def _policy(caller: _Caller, user_id: int, change: FilterPolicyUpdate):
    """Enforce rules and severities on one account after fresh authentication; audited."""
    now = utc_now()
    try:
        target = caller.account(user_id, C.FILTERS_POLICY_ENFORCE)
    except HiddenAccount as hidden:
        if hidden.account is not None:
            event = _policy_event(caller.access, now, user_id, AuditOutcome.DENIED)
            _refused(caller.db, event, hidden)
        raise
    if target.role is Role.OWNER:
        raise FilterPreferenceConflict(
            "owner_filter_policy", "The owner's filter policy cannot be enforced"
        )
    try:
        fresh = _reauthenticate(caller, target, change.reauthentication)
    except AccessDenied as denial:
        _refused(
            caller.db,
            _policy_event(caller.access, now, target.user_id, AuditOutcome.DENIED),
            denial,
        )
        raise
    with operations.locked(
        caller, C.FILTERS_POLICY_ENFORCE, target, AuditAction.FILTER_POLICY_CHANGE, fresh=fresh
    ) as current:
        policy_change = filter_enforcement.set_enforcement(
            caller.db, current.user_id, change, now=now
        )
        if policy_change is not None:
            stage_event(
                caller.db,
                _policy_event(
                    caller.access,
                    now,
                    current.user_id,
                    AuditOutcome.SUCCEEDED,
                    cause=policy_change.cause.value,
                    policy_revision=policy_change.policy_revision,
                    rules=len(change.rules),
                    severities=len(change.severities),
                ),
            )
        caller.db.commit()
    return filter_preferences.filter_preferences_view(caller.db, target.user_id)


@router.get("/default-mode", response_model=DefaultMode)
def get_default_mode(caller: _Caller = Depends(_caller)):
    """The mode new accounts start in."""
    return DefaultMode(mode=filter_preferences.default_mode(caller.db))


@router.put("/default-mode", response_model=DefaultMode)
def put_default_mode(change: DefaultModeUpdate, caller: _Caller = Depends(_caller)):
    """Change the mode new accounts start in; existing accounts keep theirs."""
    require(caller.access, C.FILTERS_INSTANCE_MODE_EDIT)
    return DefaultMode(mode=filter_preferences.set_default_mode(caller.db, change.mode))


@router.get("/personal", response_model=FilterPreferencesView)
def get_own(caller: _Caller = Depends(_caller)):
    """The caller's effective defaults; every account may read its own."""
    return _respond(
        lambda: filter_preferences.filter_preferences_view(caller.db, caller.own.user_id)
    )


@router.put("/personal", response_model=FilterPreferencesView)
def put_own(change: PreferencesUpdate, caller: _Caller = Depends(_caller)):
    """Replace the caller's values for their current mode."""
    return _respond(lambda: _save(caller, caller.own, change))


@router.put("/personal/mode", response_model=FilterPreferencesView)
def put_own_mode(change: FilterModeUpdate, caller: _Caller = Depends(_caller)):
    """Choose the caller's mode; refused while a policy is enforced on the caller."""
    return _respond(lambda: _mode(caller, caller.own, change))


@router.get("/users/{user_id}", response_model=FilterPreferencesView)
def get_user(user_id: int, caller: _Caller = Depends(_caller)):
    """Another account's effective defaults, for those who manage its values, mode, or policy."""
    return _respond(lambda: _view_other(caller, caller.account(user_id, *_VIEWERS)))


@router.put("/users/{user_id}", response_model=FilterPreferencesView)
def put_user(user_id: int, change: PreferencesUpdate, caller: _Caller = Depends(_caller)):
    """Replace another account's values (owner by default)."""
    return _respond(lambda: _save(caller, caller.account(user_id, _VALUES), change))


@router.put("/users/{user_id}/mode", response_model=FilterPreferencesView)
def put_user_mode(user_id: int, change: FilterModeUpdate, caller: _Caller = Depends(_caller)):
    """Assign an account's mode; enforcement still applies in either mode."""
    return _respond(lambda: _mode(caller, caller.account(user_id, _MODE), change))


@router.put("/users/{user_id}/policy", response_model=FilterPreferencesView)
def put_user_policy(user_id: int, change: FilterPolicyUpdate, caller: _Caller = Depends(_caller)):
    """Replace the rules and severities enforced on an account (owner, fresh password)."""
    return _respond(lambda: _policy(caller, user_id, change))
