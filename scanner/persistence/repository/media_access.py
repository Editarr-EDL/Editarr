"""Stored local media scopes.

Readers return stored values unparsed; `scanner.security.media_access` parses
them and fails closed on anything unreadable. Writers commit, are idempotent,
and bump `users.permission_revision` for every account whose access changed,
so durable jobs and clients notice. Runtime foreign keys are off, so deleting a
scope deletes its sources here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, String, delete, insert, or_, select, type_coerce, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from scanner.persistence.rows.db_auth import UserProviderIdentityRow, UserRow
from scanner.persistence.rows.db_policies import (
    LOCAL_MEDIA_DEFAULT_SCOPE_ID,
    MediaAccessScopeRow,
    MediaAccessScopeSourceRow,
)

SourceValue = tuple[str, str]
"""A stored source: its kind and Jellyfin library ID."""

@dataclass(frozen=True)
class StoredScope:
    """The scope that applies to one account, as stored.

    `all_sources` is the raw stored value, so a corrupt flag reaches the parser.
    """

    inherited: bool
    all_sources: object
    sources: frozenset[SourceValue]


def read_scope(db: Session, user_id: int | None) -> StoredScope | None:
    """Read a user's individual scope, else the default, in one statement.

    `user_id=None` reads the default. Returns None only when the default row
    is missing, which grants nothing.
    """

    rows = db.execute(
        select(
            MediaAccessScopeRow.id,
            MediaAccessScopeRow.user_id,
            type_coerce(MediaAccessScopeRow.all_sources, Integer).label("all_sources"),
            type_coerce(MediaAccessScopeSourceRow.source_kind, String).label("kind"),
            MediaAccessScopeSourceRow.source_id,
        )
        .outerjoin(
            MediaAccessScopeSourceRow, MediaAccessScopeSourceRow.scope_id == MediaAccessScopeRow.id
        )
        .where(
            or_(
                MediaAccessScopeRow.id == LOCAL_MEDIA_DEFAULT_SCOPE_ID,
                MediaAccessScopeRow.user_id == user_id,
            )
        )
    ).all()
    own = [row for row in rows if row.user_id is not None]
    chosen = own or [row for row in rows if row.id == LOCAL_MEDIA_DEFAULT_SCOPE_ID]
    if not chosen:
        return None
    return StoredScope(
        inherited=not own,
        all_sources=chosen[0].all_sources,
        sources=frozenset((row.kind, row.source_id) for row in chosen if row.kind is not None),
    )


def _bump(db: Session, *conditions) -> None:
    db.execute(
        update(UserRow)
        .where(*conditions)
        .values(permission_revision=UserRow.permission_revision + 1)
    )


def _inheriting_local_users():
    individual = select(MediaAccessScopeRow.user_id).where(MediaAccessScopeRow.user_id.is_not(None))
    linked = select(UserProviderIdentityRow.user_id)
    return (UserRow.role != "owner", UserRow.id.not_in(individual), UserRow.id.not_in(linked))


def replace_scope(
    db: Session,
    user_id: int | None,
    *,
    all_sources: bool,
    sources: Iterable[SourceValue],
    now: datetime,
) -> bool:
    """Store a complete scope: the default when `user_id` is None, else the user's selection.

    Returns False and writes nothing when the stored scope already equals it.
    """

    wanted = frozenset(() if all_sources else sources)
    current = read_scope(db, user_id)
    if (
        current is not None
        and current.inherited == (user_id is None)
        and current.all_sources == int(all_sources)
        and current.sources == wanted
    ):
        return False
    values = {"all_sources": all_sources, "updated_at": now}
    if user_id is None:
        scope_id = LOCAL_MEDIA_DEFAULT_SCOPE_ID
        db.execute(
            update(MediaAccessScopeRow).where(MediaAccessScopeRow.id == scope_id).values(values)
        )
    else:
        # An upsert, so two first selections for one user cannot collide on the unique user.
        scope_id = db.execute(
            sqlite_insert(MediaAccessScopeRow)
            .values(user_id=user_id, **values)
            .on_conflict_do_update(index_elements=["user_id"], set_=values)
            .returning(MediaAccessScopeRow.id)
        ).scalar_one()
    db.execute(
        delete(MediaAccessScopeSourceRow).where(MediaAccessScopeSourceRow.scope_id == scope_id)
    )
    if wanted:
        db.execute(
            insert(MediaAccessScopeSourceRow),
            [
                {"scope_id": scope_id, "source_kind": kind, "source_id": source_id}
                for kind, source_id in wanted
            ],
        )
    if user_id is None:
        _bump(db, *_inheriting_local_users())
    else:
        _bump(db, UserRow.id == user_id)
    db.commit()
    return True


def clear_scope(db: Session, user_id: int) -> bool:
    """Return a user to the live default. False when the user already inherits it."""

    scope_id = db.scalar(
        select(MediaAccessScopeRow.id).where(MediaAccessScopeRow.user_id == user_id)
    )
    if scope_id is None:
        return False
    db.execute(
        delete(MediaAccessScopeSourceRow).where(MediaAccessScopeSourceRow.scope_id == scope_id)
    )
    db.execute(delete(MediaAccessScopeRow).where(MediaAccessScopeRow.id == scope_id))
    _bump(db, UserRow.id == user_id)
    db.commit()
    return True
