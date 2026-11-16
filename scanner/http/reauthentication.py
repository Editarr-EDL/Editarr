"""Fresh interactive authentication: one guard for every sensitive action.

Callers pass the action, its resolved target, and the request's optional
`Reauthentication`. The guard asks the evaluator first, so an account without
the capability or an API key is refused before any password check. Only then
does it throttle by account and client address, have Jellyfin verify the
account's password in this request, and return the capability. The proof never
outlives the request (see `access.fresh`).

Consumers: API key reveal and rotation, permission changes, linking, ownership
transfer, account deletion, and restore.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from scanner.security.access.api_shapes import AccessErrorCode, Reauthentication
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.fresh import FreshAuthentication, verify_reauthentication
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.targets import Action, Target
from scanner.security.jellyfin_accounts.reauthentication import check_linked_password

from .auth_rate_limit import LoginRateLimiter


class ReauthenticationGuard:
    """Decide a sensitive action, confirming the password only when the decision needs it."""

    def __init__(self, limiter: LoginRateLimiter | None = None) -> None:
        self.limiter = limiter or LoginRateLimiter()

    def authorize(
        self,
        db: Session,
        access: PrincipalAccess,
        action: Action,
        target: Target,
        proof: Reauthentication | None,
        *,
        address: str,
    ) -> Capability:
        """Return the capability exercised or raise `AccessDenied`.

        Wrong passwords count against the account and the client address;
        a full bucket answers `reauthentication_throttled` without checking. A
        Jellyfin outage answers `jellyfin_unavailable` and never counts.
        """

        decision = decide(access, action, target)
        if isinstance(decision, Allowed):
            return decision.capability
        if decision.code is not AccessErrorCode.REAUTHENTICATION_REQUIRED or proof is None:
            raise AccessDenied(decision.code)
        return require(access, action, target, fresh=self.prove(db, access, proof, address=address))

    def prove(
        self, db: Session, access: PrincipalAccess, proof: Reauthentication, *, address: str
    ) -> FreshAuthentication:
        """Throttle, then verify the password once for decisions that share this request.

        Callers decide first without a proof, as `authorize` does, so the password
        is checked only for an action the caller may otherwise take.
        """

        account = str(access.principal.user_id)
        retry_after = self.limiter.retry_after(username=account, address=address)
        if retry_after:
            raise AccessDenied(AccessErrorCode.REAUTHENTICATION_THROTTLED, retry_after=retry_after)
        try:
            fresh = verify_reauthentication(db, access, proof, check_linked_password)
        except AccessDenied as denial:
            if denial.code is AccessErrorCode.REAUTHENTICATION_FAILED:
                self.limiter.record_failure(username=account, address=address)
            raise
        self.limiter.clear_username(account)
        return fresh


reauthentication_guard = ReauthenticationGuard()
