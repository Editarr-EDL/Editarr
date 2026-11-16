"""Jellyfin users kept out of Editarr after their accounts were deleted.

A block is keyed by Jellyfin server and canonical user ID, like a link, and is
never matched by name. Deleting a linked account with `block` stages one in the
deletion's transaction. Sign-in, Quick Connect, import, and linking refuse a
blocked identity under the write lock that would create its account, so a blocked
identity never has a linked account. Unblocking removes the row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from scanner.formats.export_layout import JellyfinUserId
from scanner.persistence.rows.db_auth import (
    JellyfinAccountSyncRow,
    JellyfinUserBlockRow,
    UserProviderIdentityRow,
)
from scanner.security.access.identity import JellyfinServerId, ProviderIdentity


@dataclass(frozen=True)
class Block:
    identity: ProviderIdentity
    name: str | None
    """Jellyfin's name for the user when the account was deleted."""
    blocked_by: int | None
    blocked_at: datetime


def _matches(identity: ProviderIdentity):
    return (
        JellyfinUserBlockRow.server_id == identity.server_id,
        JellyfinUserBlockRow.provider_user_id == identity.user_id.value,
    )


def is_blocked(db: Session, identity: ProviderIdentity) -> bool:
    return bool(db.scalar(select(exists().where(*_matches(identity)))))


def block_linked(db: Session, user_id: int, *, actor: int, now: datetime) -> bool:
    """Stage a block of the Jellyfin user linked to `user_id`; False when it is not linked.

    Call before the deletion releases the link, in the deletion's transaction.
    """

    row = db.execute(
        select(UserProviderIdentityRow, JellyfinAccountSyncRow.jellyfin_name)
        .outerjoin(
            JellyfinAccountSyncRow,
            JellyfinAccountSyncRow.user_id == UserProviderIdentityRow.user_id,
        )
        .where(UserProviderIdentityRow.user_id == user_id)
    ).first()
    if row is None:
        return False
    identity = row.UserProviderIdentityRow
    db.execute(
        sqlite_insert(JellyfinUserBlockRow)
        .values(
            server_id=identity.server_id,
            provider_user_id=identity.provider_user_id,
            name=row.jellyfin_name,
            blocked_by=actor,
            blocked_at=now.astimezone(UTC).replace(tzinfo=None),
        )
        .on_conflict_do_nothing()
    )
    return True


def blocked_users(db: Session) -> list[Block]:
    """Every block, newest first."""

    rows = db.scalars(
        select(JellyfinUserBlockRow).order_by(
            JellyfinUserBlockRow.blocked_at.desc(), JellyfinUserBlockRow.provider_user_id
        )
    )
    return [
        Block(
            ProviderIdentity(JellyfinServerId(row.server_id), JellyfinUserId(row.provider_user_id)),
            row.name,
            row.blocked_by,
            row.blocked_at.replace(tzinfo=UTC),
        )
        for row in rows
    ]


def unblock(db: Session, identity: ProviderIdentity) -> bool:
    """Stage the block's removal; False when the identity was not blocked."""

    return db.execute(delete(JellyfinUserBlockRow).where(*_matches(identity))).rowcount > 0
