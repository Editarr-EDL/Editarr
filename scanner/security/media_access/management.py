"""Authorized changes to local media scopes.

Each change checks the caller's live capability with fresh interactive
authentication before it reveals anything about the target account. Admins
hold the scope capabilities by default; the owner can remove them through role
defaults or overrides. MU-15 exposes these over HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.persistence.repository import media_access as storage
from scanner.persistence.rows.db_auth import UserProviderIdentityRow, UserRow
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.fresh import FreshAuthentication
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.targets import INSTANCE, AccountRef
from scanner.security.access.vocabulary import Role

from .keys import SourceRef


class UnsupportedMediaAccessChange(ValueError):
    """A local scope change for a linked account, whose media Jellyfin decides."""

    code = "linked_account"


@dataclass(frozen=True)
class ScopeChoice:
    """A complete local scope: every source, or exactly the listed ones."""

    all_sources: bool
    sources: frozenset[SourceRef] = frozenset()

    def __post_init__(self) -> None:
        if self.all_sources and self.sources:
            raise ValueError("A scope with every source lists none")

    def stored(self) -> list[storage.SourceValue]:
        return [(source.kind.value, source.source_id) for source in self.sources]


def _account(
    db: Session,
    access: PrincipalAccess,
    user_id: int,
    capability: Capability,
    fresh: FreshAuthentication | None,
) -> bool:
    """Require `capability` on another account; whether that account is linked.

    A missing account is checked as a plain user first, so a caller learns of
    its absence only after passing the checks an existing account would need.
    """

    row = db.execute(
        select(UserRow.role, UserProviderIdentityRow.user_id.label("linked"))
        .outerjoin(UserProviderIdentityRow, UserProviderIdentityRow.user_id == UserRow.id)
        .where(UserRow.id == user_id)
    ).first()
    if user_id == access.principal.user_id:
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    require(
        access, capability, AccountRef(user_id, Role(row.role) if row else Role.USER), fresh=fresh
    )
    if row is None:
        raise AccessDenied(AccessErrorCode.NOT_FOUND)
    return row.linked is not None


def set_default_scope(
    db: Session,
    access: PrincipalAccess,
    choice: ScopeChoice,
    *,
    fresh: FreshAuthentication | None,
    now: datetime,
) -> bool:
    """Replace the live default. Every local account without its own selection follows it."""

    require(access, Capability.MEDIA_ACCESS_DEFAULTS_EDIT, INSTANCE, fresh=fresh)
    return storage.replace_scope(
        db, None, all_sources=choice.all_sources, sources=choice.stored(), now=now
    )


def set_user_scope(
    db: Session,
    access: PrincipalAccess,
    user_id: int,
    choice: ScopeChoice | None,
    *,
    fresh: FreshAuthentication | None,
    now: datetime,
) -> bool:
    """Store a local account's own selection, or return it to the default with `None`."""

    if _account(db, access, user_id, Capability.MEDIA_ACCESS_USER_EDIT, fresh):
        raise UnsupportedMediaAccessChange
    if choice is None:
        return storage.clear_scope(db, user_id)
    return storage.replace_scope(
        db, user_id, all_sources=choice.all_sources, sources=choice.stored(), now=now
    )
