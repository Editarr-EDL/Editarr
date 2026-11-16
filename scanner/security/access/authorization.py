"""The shared authorization interface for HTTP handlers, services, workers, and serializers.

Every caller resolves its target, then uses one of these entry points.
`require_principal` and `authorize_job` load the account, its role defaults,
and its overrides as stored now, from one snapshot. `require`, `may_attempt`,
and `capability_payload` decide from a principal already loaded, so one
request's decisions share the snapshot it loaded. Nothing is cached between
requests or job runs.
"""

from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from scanner.db import UserRow
from scanner.persistence.rows.db_account_dispositions import AccountDispositionRow

from .api_shapes import AccessErrorCode, CapabilityPayload, PrincipalSummary
from .capability_ids import Capability
from .catalog import CAPABILITY_CONTRACT_VERSION
from .decisions import AccessDenied, Allowed, Decision, Denied
from .evaluator import attemptable, decide
from .fresh import FreshAuthentication
from .grants import PrincipalAccess
from .identity import CredentialKind, UserId
from .load import load_access
from .targets import INSTANCE, AccountRef, Action, ActionPair, Target
from .vocabulary import Role


def require(
    access: PrincipalAccess,
    action: Action,
    target: Target = INSTANCE,
    *,
    fresh: FreshAuthentication | None = None,
) -> Capability:
    """Return the capability exercised, or raise `AccessDenied` with the decision's code."""

    decision = decide(access, action, target, fresh=fresh)
    if isinstance(decision, Denied):
        raise AccessDenied(decision.code)
    return decision.capability


class HiddenAccount(AccessDenied):
    """`not_found` for an account ID the caller may not learn exists.

    `account` is the stored account, or None when the ID names nothing. It is for
    audit records only and never reaches the response.
    """

    def __init__(self, account: AccountRef | None) -> None:
        super().__init__(AccessErrorCode.NOT_FOUND)
        self.account = account


def resolve_account(
    db: Session, access: PrincipalAccess, user_id: int, *actions: Action
) -> AccountRef:
    """The account a route's `user_id` names, for a caller who may learn whether it exists.

    The caller's own ID always resolves. Another ID resolves only for a caller who
    holds `accounts.list` or the other-account capability of one of `actions`, the
    route's possible decisions. Anyone else gets `HiddenAccount` whether or not the
    account exists, so the answer reveals nothing about which IDs are in use. A
    deleted account (MU-38) is a tombstone and never resolves.
    """

    principal = access.principal
    if user_id == principal.user_id:
        return AccountRef(principal.user_id, principal.account.role)
    role = db.scalar(
        select(UserRow.role).where(
            UserRow.id == user_id,
            ~exists().where(AccountDispositionRow.user_id == UserRow.id),
        )
    )
    account = None if role is None else AccountRef(UserId(user_id), Role(role))
    reveal = {action.other if isinstance(action, ActionPair) else action for action in actions}
    if account is None or access.capabilities.isdisjoint(reveal | {Capability.ACCOUNTS_LIST}):
        raise HiddenAccount(account)
    return account


def may_attempt(access: PrincipalAccess, action: Action, target: Target = INSTANCE) -> bool:
    """For serializers and UI hints: allowed now, or allowed once the user confirms a password."""

    decision = decide(access, action, target)
    return (
        isinstance(decision, Allowed) or decision.code is AccessErrorCode.REAUTHENTICATION_REQUIRED
    )


def require_principal(
    db: Session, user_id: UserId, credential: CredentialKind, session_id: int | None = None
) -> PrincipalAccess:
    """Load the live principal or raise `authentication_required` when the account is gone."""

    access = load_access(db, user_id, credential, session_id)
    if access is None:
        raise AccessDenied(AccessErrorCode.AUTHENTICATION_REQUIRED)
    return access


def authorize_job(
    db: Session,
    initiator: UserId,
    credential: CredentialKind,
    action: Action,
    target: Target = INSTANCE,
) -> Decision:
    """Recheck a durable job's initiator when the job runs.

    `credential` is the kind recorded when the job was queued. A job has no
    session, so it never satisfies fresh interactive authentication.
    """

    access = load_access(db, initiator, credential)
    if access is None:
        return Denied(AccessErrorCode.AUTHENTICATION_REQUIRED)
    return decide(access, action, target)


def capability_payload(access: PrincipalAccess) -> CapabilityPayload:
    """The safe client payload: own state, permission revision, and attemptable capabilities."""

    account = access.principal.account
    return CapabilityPayload(
        version=CAPABILITY_CONTRACT_VERSION,
        permission_revision=access.principal.permission_revision,
        principal=PrincipalSummary(
            user_id=account.user_id,
            role=account.role,
            credential=access.principal.credential,
            status=account.status,
            link_state=account.link_state,
        ),
        capabilities=sorted(attemptable(access)),
    )
