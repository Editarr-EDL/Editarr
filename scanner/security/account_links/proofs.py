"""Single-use account-link proofs bound to the actor, its session, the target, and the identity.

A proof records that, in one fresh-authenticated request, the actor showed a Jellyfin
sign-in for one identity and the target account's ownership on its `ProofBasis`. Only
the token's SHA-256 is kept, in memory, so a restart voids every outstanding proof.
Presenting a token for another actor, session, or target destroys it. The proof also
keeps the `ProofBinding` it was issued against; linking rereads it before the files
are written and again at the switch, so any change in between makes the proof stale.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import UserProviderIdentityRow, UserRow
from scanner.formats.export_layout import JellyfinUserId
from scanner.security.access.identity import JellyfinServerId, ProviderIdentity

PROOF_LIFETIME = timedelta(minutes=10)


class ProofRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProofBasis(StrEnum):
    """How the target account's ownership was shown, recorded in the preview and audit.

    Stored audit rows may also name `account_password`, the target's own local
    password, which first links of another account used before 0030.
    """

    ACTOR_AUTHENTICATION = "actor_authentication"
    """The actor links its own account; its fresh authentication proves it."""
    ADMIN_ATTESTED = "admin_attested"
    """The actor links another account on the actor's own fresh authentication."""


def proof_basis(actor_id: int, target_user_id: int) -> ProofBasis:
    """Decide how the target's ownership is shown."""
    if actor_id == target_user_id:
        return ProofBasis.ACTOR_AUTHENTICATION
    return ProofBasis.ADMIN_ATTESTED


def stored_identity(db: Session, user_id: int) -> ProviderIdentity | None:
    row = db.get(UserProviderIdentityRow, user_id)
    if row is None:
        return None
    return ProviderIdentity(JellyfinServerId(row.server_id), JellyfinUserId(row.provider_user_id))


@dataclass(frozen=True)
class ProofBinding:
    """The target state a proof was issued against."""

    permission_revision: int
    current: ProviderIdentity | None
    """The target's identity when proven."""
    identity: ProviderIdentity
    """The proven Jellyfin identity."""


def read_binding(db: Session, user_id: int, identity: ProviderIdentity) -> ProofBinding:
    """Read the target's binding for `identity` in the caller's transaction."""
    revision = db.scalar(select(UserRow.permission_revision).where(UserRow.id == user_id))
    return ProofBinding(revision, stored_identity(db, user_id), identity)


@dataclass(frozen=True)
class LinkProof:
    actor_id: int
    session_id: str
    target_user_id: int
    basis: ProofBasis
    binding: ProofBinding
    expires_at: datetime

    @property
    def identity(self) -> ProviderIdentity:
        return self.binding.identity


def _key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


class ProofRegistry:
    def __init__(self) -> None:
        self._proofs: dict[str, LinkProof] = {}
        self._lock = threading.Lock()

    def mint(self, proof: LinkProof, now: datetime) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            live = self._proofs.items()
            self._proofs = {key: kept for key, kept in live if kept.expires_at > now}
            self._proofs[_key(token)] = proof
        return token

    def _bound(self, token: str, actor_id: int, session_id: str, target: int, now) -> LinkProof:
        key = _key(token)
        proof = self._proofs.get(key)
        if proof is None:
            raise ProofRejected("link_proof_invalid")
        if (proof.actor_id, proof.session_id, proof.target_user_id) != (
            actor_id,
            session_id,
            target,
        ):
            del self._proofs[key]
            raise ProofRejected("link_proof_invalid")
        if proof.expires_at <= now:
            del self._proofs[key]
            raise ProofRejected("link_proof_expired")
        return proof

    def check(
        self, token: str, *, actor_id: int, session_id: str, target_user_id: int, now: datetime
    ) -> LinkProof:
        """Return the proof without spending it, or raise `ProofRejected`."""
        with self._lock:
            return self._bound(token, actor_id, session_id, target_user_id, now)

    def take(
        self, token: str, *, actor_id: int, session_id: str, target_user_id: int, now: datetime
    ) -> LinkProof:
        """Spend the proof; a second take of the same token is refused."""
        with self._lock:
            proof = self._bound(token, actor_id, session_id, target_user_id, now)
            del self._proofs[_key(token)]
            return proof


proofs = ProofRegistry()
