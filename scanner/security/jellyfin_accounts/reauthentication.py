"""Fresh interactive authentication: Jellyfin checks a linked account's password.

The password is proven against the linked Jellyfin server under the user's last
verified Jellyfin name, and only counts when Jellyfin answers with the linked
identity itself, so a renamed or reassigned name can never prove another user.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from scanner.integrations.jellyfin_user_snapshot import (
    CredentialProof,
    Unverified,
    UnverifiedReason,
)
from scanner.security.access.fresh import PasswordCheck

from . import store
from .admission import encodable
from .sync import open_adapter


def check_linked_password(db: Session, user_id: int, password: str) -> PasswordCheck:
    """Ask Jellyfin whether `password` is this account's linked, enabled identity's password."""

    record = store.read_link(db, user_id)
    if record is None or not encodable(password):
        return PasswordCheck.REFUSED
    if record.name is None:
        return PasswordCheck.UNNAMED
    adapter = open_adapter(db)
    if adapter is None:
        return PasswordCheck.UNAVAILABLE
    with adapter:
        proof = adapter.authenticate(record.name, password)
    if isinstance(proof, Unverified):
        rejected = proof.reason is UnverifiedReason.CREDENTIALS_REJECTED
        return PasswordCheck.REFUSED if rejected else PasswordCheck.UNAVAILABLE
    accepted = (
        isinstance(proof, CredentialProof)
        and proof.user.identity == record.identity
        and not proof.user.is_disabled
    )
    return PasswordCheck.ACCEPTED if accepted else PasswordCheck.REFUSED
