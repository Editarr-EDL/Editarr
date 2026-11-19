"""Account directory reads and account writes for the Users API.

Writers stage their change without committing, so the caller commits it with
its audit event. Each write targets one account by its stable ID; the caller
has already authorized that target.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import Select, delete, exists, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from scanner.models import canonicalize_username
from scanner.persistence.rows.db_account_dispositions import AccountDispositionRow
from scanner.persistence.rows.db_auth import (
    UserAvatarRow,
    UserCapabilityOverrideRow,
    UserProfileRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.persistence.rows.db_policies import (
    MediaAccessScopeRow,
    UserFilterEnforcementRow,
)
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.identity import AccountStatus, DisableCause
from scanner.security.access.vocabulary import Role
from scanner.security.account_permissions import applied_override

from .account_avatars import Avatar
from .auth_repository_sessions import revoke_user_sessions

PROFILE_FIELDS = ("display_name", "language")
"""Optional profile values, each validated by `account_profiles` before storage."""

JELLYFIN_PROFILE_FIELDS = ("username", "display_name", "avatar")
"""What Jellyfin provides for a linked account, refreshed at each sign-in and sync and never
edited in Editarr. Language stays editable."""


class UsernameRefresh(StrEnum):
    UNCHANGED = "unchanged"
    REFRESHED = "refreshed"
    UNAVAILABLE = "unavailable"
    """Another account holds the name, or it is no valid username; the account keeps its own."""


class AccountSource(StrEnum):
    LOCAL = "local"
    JELLYFIN = "jellyfin"


@dataclass(frozen=True)
class AccountRecord:
    """One account as the directory shows it, plus the fields only a detail read returns."""

    user_id: int
    username: str | None
    display_name: str | None
    language: str | None
    source: AccountSource
    role: Role
    status: AccountStatus
    disable_cause: DisableCause | None
    created_at: datetime
    last_login_at: datetime | None
    avatar_digest: str | None
    permission_overrides: bool
    media_access_override: bool
    filter_policy_override: bool


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _stored(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _records() -> Select:
    user = UserRow.id
    return (
        select(
            UserRow,
            UserProfileRow,
            UserAvatarRow.digest,
            UserProviderIdentityRow.user_id.label("linked"),
            exists()
            .where(UserCapabilityOverrideRow.user_id == user, applied_override())
            .label("overrides"),
            exists().where(MediaAccessScopeRow.user_id == user).label("media"),
            exists().where(UserFilterEnforcementRow.user_id == user).label("filters"),
        )
        .outerjoin(UserProfileRow, UserProfileRow.user_id == user)
        .outerjoin(UserAvatarRow, UserAvatarRow.user_id == user)
        .outerjoin(UserProviderIdentityRow, UserProviderIdentityRow.user_id == user)
    )


def _record(row) -> AccountRecord:
    user, profile = row.UserRow, row.UserProfileRow
    value = (lambda name: getattr(profile, name)) if profile is not None else (lambda _: None)
    return AccountRecord(
        user_id=user.id,
        username=user.username,
        display_name=value("display_name"),
        language=value("language"),
        source=AccountSource.JELLYFIN if row.linked is not None else AccountSource.LOCAL,
        role=Role(user.role),
        status=AccountStatus(user.status),
        disable_cause=DisableCause(user.disable_cause) if user.disable_cause else None,
        created_at=_utc(user.created_at),
        last_login_at=_utc(value("last_login_at")),
        avatar_digest=row.digest,
        permission_overrides=bool(row.overrides),
        media_access_override=bool(row.media),
        filter_policy_override=bool(row.filters),
    )


def list_accounts(
    db: Session, *, offset: int, limit: int, search: str | None
) -> tuple[int, list[AccountRecord]]:
    """One page of accounts in ID order and the total matching `search`.

    `search` matches part of the canonical username or, ignoring ASCII case, the
    display name. Deleted accounts are not listed.
    """

    query = _records().where(~exists().where(AccountDispositionRow.user_id == UserRow.id))
    if search:
        needle = search.strip()
        query = query.where(
            or_(
                UserRow.normalized_username.contains(needle.casefold(), autoescape=True),
                UserProfileRow.display_name.icontains(needle, autoescape=True),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(UserRow.id).offset(offset).limit(limit)).all()
    return total, [_record(row) for row in rows]


def read_account(db: Session, user_id: int) -> AccountRecord | None:
    row = db.execute(_records().where(UserRow.id == user_id)).first()
    return _record(row) if row is not None else None


def is_linked(db: Session, user_id: int) -> bool:
    """Whether a Jellyfin identity is linked; its name and avatar then come from Jellyfin."""

    return bool(db.scalar(select(exists().where(UserProviderIdentityRow.user_id == user_id))))


def update_profile(
    db: Session, user_id: int, changes: Mapping[str, str | None], *, now: datetime
) -> None:
    """Set the listed profile fields; `None` clears one. Other fields keep their values."""

    if not set(changes) <= set(PROFILE_FIELDS):
        raise ValueError("Unknown profile field")
    values = {**changes, "updated_at": _stored(now)}
    db.execute(
        sqlite_insert(UserProfileRow)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(index_elements=[UserProfileRow.user_id], set_=values)
    )


def adopt_jellyfin_name(db: Session, user_id: int, name: str, *, now: datetime) -> UsernameRefresh:
    """Make a linked account's username its Jellyfin user's Name, under the caller's write lock.

    Sessions stay: the name came from Jellyfin, not from someone editing the account.
    """

    try:
        display, canonical = canonicalize_username(name)
    except ValueError:
        return UsernameRefresh.UNAVAILABLE
    if db.scalar(select(UserRow.username).where(UserRow.id == user_id)) == display:
        return UsernameRefresh.UNCHANGED
    held = select(UserRow.id).where(UserRow.normalized_username == canonical, UserRow.id != user_id)
    if db.scalar(held) is not None:
        return UsernameRefresh.UNAVAILABLE
    db.execute(
        update(UserRow)
        .where(UserRow.id == user_id)
        .values(username=display, normalized_username=canonical, updated_at=_stored(now))
    )
    return UsernameRefresh.REFRESHED


def read_avatar(db: Session, user_id: int) -> Avatar | None:
    row = db.get(UserAvatarRow, user_id)
    return Avatar(row.digest, bytes(row.image)) if row is not None else None


def set_avatar(db: Session, user_id: int, avatar: Avatar, *, now: datetime) -> None:
    values = {"digest": avatar.digest, "image": avatar.image, "updated_at": _stored(now)}
    db.execute(
        sqlite_insert(UserAvatarRow)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(index_elements=[UserAvatarRow.user_id], set_=values)
    )


def remove_avatar(db: Session, user_id: int) -> bool:
    return db.execute(delete(UserAvatarRow).where(UserAvatarRow.user_id == user_id)).rowcount > 0


def _refuse_owner(db: Session, user_id: int) -> None:
    """Only an ownership transfer changes the owner's role or status."""

    if db.scalar(select(UserRow.role).where(UserRow.id == user_id)) == Role.OWNER.value:
        raise AccessDenied(AccessErrorCode.PROTECTED_ACCOUNT)


def set_disable_cause(
    db: Session, user_id: int, cause: DisableCause | None, *, now: datetime
) -> int:
    """Disable an account for `cause`, or enable it when `cause` is None. Returns sessions ended.

    Disabling ends the account's sessions; enabling never restores them. A
    disabled account keeps its data, key, and history; its key stops working
    while it stays disabled. Disabling the owner is refused (`protected_account`).
    """

    if cause is not None:
        _refuse_owner(db, user_id)
    status = AccountStatus.ACTIVE if cause is None else AccountStatus.DISABLED
    stored = cause.value if cause is not None else None
    db.execute(
        update(UserRow)
        .where(
            UserRow.id == user_id,
            or_(UserRow.status != status.value, UserRow.disable_cause.is_distinct_from(stored)),
        )
        .values(
            status=status.value,
            disable_cause=stored,
            updated_at=_stored(now),
            permission_revision=UserRow.permission_revision + 1,
        )
    )
    if cause is not None:
        return revoke_user_sessions(db, user_id, now=now, commit=False)
    return 0
