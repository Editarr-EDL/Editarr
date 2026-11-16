"""Revalidate request authorization while the caller holds the database write lock."""

from sqlalchemy.orm import Session

from .api_shapes import AccessErrorCode
from .authorization import require, resolve_account
from .decisions import AccessDenied, Denied
from .evaluator import decide
from .fresh import FreshAuthentication
from .grants import PrincipalAccess
from .load import load_access
from .targets import AccountRef, Action, Target


def revalidate(
    db: Session,
    access: PrincipalAccess,
    action: Action,
    target: Target,
    *,
    fresh: FreshAuthentication | None = None,
    stale_code: AccessErrorCode = AccessErrorCode.CAPABILITY_DENIED,
) -> Target:
    """Resolve current protection and refuse stale authority before any effect.

    Call under `write_lock` and hold it through the protected read/write and commit.
    Password verification stays outside the lock; its proof remains request-bound.
    """

    principal = access.principal
    current = load_access(db, principal.user_id, principal.credential, access.session_id)
    if current is None:
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    if isinstance(target, AccountRef):
        target = resolve_account(db, current, target.user_id, action)
    if current.principal != principal:
        if stale_code is not AccessErrorCode.PERMISSION_REVISION_STALE:
            decision = decide(current, action, target)
            if (
                isinstance(decision, Denied)
                and decision.code is not AccessErrorCode.REAUTHENTICATION_REQUIRED
            ):
                raise AccessDenied(decision.code)
        raise AccessDenied(stale_code)
    require(access, action, target, fresh=fresh)
    return target
