"""Fresh interactive authentication proofs, minted only by a Jellyfin password check.

A proof is an object, never a request flag. `verify_reauthentication` creates
it after Jellyfin accepts the account's password and binds it to the exact
`PrincipalAccess` it verified. `request_access` builds a new principal for
every request, so the evaluator accepts the proof only in the request that
checked the password; an equal principal loaded by another request does not
match. API keys never obtain one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy.orm import Session

from .api_shapes import AccessErrorCode, Reauthentication
from .decisions import AccessDenied
from .grants import PrincipalAccess
from .identity import CredentialKind

_SEAL = object()


@dataclass(frozen=True, eq=False)
class FreshAuthentication:
    access: PrincipalAccess
    _seal: object = field(repr=False)

    def proves(self, access: PrincipalAccess) -> bool:
        """Whether this proof was minted here for this very principal, not an equal one."""

        return self._seal is _SEAL and access is self.access


class PasswordCheck(StrEnum):
    """Jellyfin's answer to a password for an account's linked identity."""

    ACCEPTED = "accepted"
    REFUSED = "refused"
    """Jellyfin refused the password, or the account has no enabled link to check it with."""
    UNNAMED = "unnamed"
    """Editarr has not stored the linked user's Jellyfin name yet, so nothing was checked."""
    UNAVAILABLE = "unavailable"
    """No Jellyfin connection is configured, or Jellyfin did not answer."""


PasswordVerifier = Callable[[Session, int, str], PasswordCheck]


def verify_reauthentication(
    db: Session,
    access: PrincipalAccess,
    proof: Reauthentication,
    verify: PasswordVerifier,
) -> FreshAuthentication:
    """Check the account's Jellyfin password with `verify` and mint a proof for this principal.

    Raises `AccessDenied` with `interactive_session_required` for API keys,
    `jellyfin_unavailable` when Jellyfin could not answer, and
    `reauthentication_failed` when Jellyfin refuses the password or the account
    has no link.
    """

    if access.principal.credential is not CredentialKind.SESSION or access.session_id is None:
        raise AccessDenied(AccessErrorCode.INTERACTIVE_SESSION_REQUIRED)
    check = verify(db, access.principal.user_id, proof.password)
    if check is PasswordCheck.UNAVAILABLE:
        raise AccessDenied(AccessErrorCode.JELLYFIN_UNAVAILABLE)
    if check is not PasswordCheck.ACCEPTED:
        raise AccessDenied(AccessErrorCode.REAUTHENTICATION_FAILED)
    return FreshAuthentication(access, _SEAL)
