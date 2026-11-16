"""The reviewed effect of linking one account to one proven Jellyfin identity.

`build_plan` reads the account, the identity's current owner, and every personal
publication, and returns a `LinkPlan`. Its preview is what the actor confirms; its
confirmation is a digest of that preview, so any change in role, media basis,
identity, exports, or proof basis before the link commits asks for a new confirmation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from scanner.db import UserProviderIdentityRow, UserRow
from scanner.formats.export_layout import ExportId, JellyfinUserId, LocalExportId
from scanner.integrations.jellyfin_user_snapshot import JellyfinUserSnapshot
from scanner.persistence.repository.export_identity_moves import PlannedMove, plan_moves
from scanner.persistence.rows.db_account_dispositions import AccountTransferMoveRow
from scanner.persistence.rows.db_account_links import (
    OPEN_STATES,
    AccountLinkTransitionRow,
    LinkKind,
)
from scanner.persistence.rows.db_auth import UserProfileRow
from scanner.security.access.identity import (
    AccountStatus,
    DisableCause,
    ProviderIdentity,
    ProviderLinkState,
)
from scanner.security.access.vocabulary import Role
from scanner.security.jellyfin_accounts import blocks, store

from .proofs import ProofBasis, stored_identity

SUSPENDED = frozenset({ProviderLinkState.SERVER_REPLACED, ProviderLinkState.OUTAGE_EXPIRED})
LINKABLE_DISABLE_CAUSES = frozenset({DisableCause.LOCAL, DisableCause.UNLINKED})
"""Disabled accounts that may still link. Linking enables an `unlinked` one; a `local`
disable stays. An upstream-disabled or deleted account cannot link."""


def linkable(status: str, cause: str | None) -> bool:
    """Whether an account with this stored status and disable cause may link."""
    return status == AccountStatus.ACTIVE.value or (
        cause is not None and DisableCause(cause) in LINKABLE_DISABLE_CAUSES
    )


def _identity(identity: ProviderIdentity | None) -> dict | None:
    if identity is None:
        return None
    return {"server_id": identity.server_id, "user_id": identity.user_id.value}


@dataclass(frozen=True)
class LinkPlan:
    user_id: int
    kind: LinkKind | None
    """None when the account is already linked on the proven server; a blocker says so."""
    current: ProviderIdentity | None
    after: ProviderIdentity
    from_export_id: ExportId
    role: Role
    media: str
    snapshot: JellyfinUserSnapshot
    display_name: str | None
    status: AccountStatus
    disable_cause: DisableCause | None
    moves: tuple[PlannedMove, ...]
    blockers: tuple[str, ...]

    @property
    def to_export_id(self) -> JellyfinUserId:
        return self.after.user_id

    @property
    def enables(self) -> bool:
        """Whether linking enables the account, which is disabled for having no link."""
        return self.disable_cause is DisableCause.UNLINKED

    def preview(self, basis: ProofBasis) -> dict:
        """What linking changes on the proof's `basis`, without host paths or secrets."""
        policy = self.snapshot.media
        return {
            "user_id": self.user_id,
            "kind": self.kind.value if self.kind else None,
            "identity": {"current": _identity(self.current), "after": _identity(self.after)},
            "export_id": {"current": self.from_export_id.value, "after": self.to_export_id.value},
            "role": {"current": self.role.value, "after": self.role.value},
            "media": {
                "current": self.media,
                "after": "jellyfin",
                "all_libraries": policy.all_libraries,
                "libraries": len(policy.library_ids),
                "parental_limit": policy.max_parental_rating is not None
                or bool(policy.blocked_tags or policy.allowed_tags or policy.blocked_unrated),
            },
            "profile": {
                "name": {"current": self.display_name, "after": self.snapshot.name},
                "avatar": "jellyfin" if self.snapshot.avatar_tag else "none",
                "synced_read_only": True,
            },
            "proof_basis": basis.value,
            "status": {
                "current": self.status.value,
                "after": AccountStatus.ACTIVE.value if self.enables else self.status.value,
                "cause": self.disable_cause.value if self.disable_cause else None,
            },
            "exports": [move.summary() for move in self.moves],
            "blockers": list(self.blockers),
        }

    def confirmation(self, basis: ProofBasis) -> str:
        document = json.dumps(self.preview(basis), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(document.encode()).hexdigest()


def _media(identity_row: UserProviderIdentityRow | None) -> str:
    if identity_row is None:
        return "local"
    if ProviderLinkState(identity_row.link_state) in SUSPENDED:
        return "suspended"
    return "jellyfin"


def build_plan(db: Session, user_id: int, snapshot: JellyfinUserSnapshot) -> LinkPlan:
    """Read the account and its publications and decide the link's effect and blockers."""
    user = db.get(UserRow, user_id)
    if user is None:
        raise LookupError(user_id)
    row = db.get(UserProviderIdentityRow, user_id)
    current = stored_identity(db, user_id)
    after = snapshot.identity
    blockers = set()
    if current is None:
        kind: LinkKind | None = LinkKind.LINK
        from_export_id: ExportId = LocalExportId(user.local_export_id)
    else:
        from_export_id = current.user_id
        kind = LinkKind.RELINK if current.server_id != after.server_id else None
        if kind is None:
            blockers.add("already_linked")
    holder = store.find_identity(db, after)
    if holder is not None and holder != user_id:
        blockers.add("jellyfin_identity_in_use")
    if snapshot.is_disabled:
        blockers.add("jellyfin_user_disabled")
    if blocks.is_blocked(db, after):
        blockers.add("jellyfin_user_blocked")
    if not linkable(user.status, user.disable_cause):
        blockers.add("account_disabled")
    if (
        db.query(AccountLinkTransitionRow.id)
        .filter(
            AccountLinkTransitionRow.user_id == user_id,
            AccountLinkTransitionRow.state.in_([state.value for state in OPEN_STATES]),
        )
        .first()
        is not None
    ):
        blockers.add("link_in_progress")
    if (
        db.query(AccountTransferMoveRow.id).filter_by(user_id=user_id, outcome=None).first()
        is not None
    ):
        blockers.add("account_transfer_unsettled")
    moves = tuple(
        move for move in plan_moves(db, user_id, after.user_id) if move.old_path != move.new_path
    )
    blockers.update(move.conflict for move in moves if move.conflict)
    profile = db.get(UserProfileRow, user_id)
    return LinkPlan(
        user_id=user_id,
        kind=kind,
        current=current,
        after=after,
        from_export_id=from_export_id,
        role=Role(user.role),
        media=_media(row),
        snapshot=snapshot,
        display_name=(profile.display_name if profile else None) or user.username,
        status=AccountStatus(user.status),
        disable_cause=DisableCause(user.disable_cause) if user.disable_cause else None,
        moves=moves,
        blockers=tuple(sorted(blockers)),
    )
