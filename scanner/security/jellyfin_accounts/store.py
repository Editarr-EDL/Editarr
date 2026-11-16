"""Read linked accounts and store Jellyfin's observations of them with their audit events.

`observe` is the only writer of Jellyfin evidence. Each observation carries the
`ReadStart` its Jellyfin read took from `reads.begin_read`, and an account keeps
the observation of its highest-generation read: an older read that answers
later (a slow sign-in, a retained listing, a delayed sync read) changes nothing,
whatever the wall clock says, and a read begun before a restore changes nothing
after it. Every write holds the SQLite write lock over
the reads it decides on, so a transition always applies to the account state it
was computed from. Writers stage the change and its event and commit once.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from scanner.formats.export_layout import JellyfinUserId
from scanner.integrations.jellyfin_user_snapshot import JellyfinUserSnapshot, MediaPolicy
from scanner.persistence.rows.db_auth import (
    JellyfinAccountSyncRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.persistence.write_lock import write_lock
from scanner.security import account_repository as accounts
from scanner.security.access.identity import (
    AccountStatus,
    DisableCause,
    JellyfinServerId,
    ProviderIdentity,
    ProviderLinkState,
    UserId,
)
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.access.vocabulary import Role
from scanner.security.account_avatars import Avatar
from scanner.security.audit.actions import ActorKind, AuditAction
from scanner.security.audit.store import stage_event

from .reads import ReadGeneration, ReadStart, clock_fence, read_epoch
from .states import (
    Evidence,
    LinkedAccount,
    SyncOutcome,
    Transition,
    UpstreamState,
    before_clock_step,
    transition,
)

DISPLAY_NAME_LIMIT = 100

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkRecord:
    """One linked account with its identity and its last stored Jellyfin state."""

    account: LinkedAccount
    identity: ProviderIdentity
    link_state: ProviderLinkState
    upstream: UpstreamState | None
    name: str | None
    avatar_tag: str | None
    generation: ReadGeneration | None
    """The read generation of the newest applied observation; None before the first."""


@dataclass(frozen=True)
class AvatarChange:
    """A fetched Jellyfin avatar for `tag`; `avatar` None removes the stored one."""

    tag: str | None
    avatar: Avatar | None


@dataclass(frozen=True)
class SyncResult:
    """One account's stable per-user sync result."""

    user_id: int
    outcome: SyncOutcome
    link_state: ProviderLinkState
    role: Role
    status: AccountStatus
    failure: str | None


def stored_time(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def utc(value: datetime | None) -> datetime | None:
    """A stored naive UTC time as an aware one."""

    return value.replace(tzinfo=UTC) if value is not None else None


def encode_policy(policy: MediaPolicy) -> str:
    """Canonical JSON of a verified media policy, for last-verified access during outages."""

    return json.dumps(
        {
            "all_libraries": policy.all_libraries,
            "library_ids": sorted(policy.library_ids),
            "max_parental_rating": policy.max_parental_rating,
            "max_parental_sub_rating": policy.max_parental_sub_rating,
            "blocked_tags": list(policy.blocked_tags),
            "allowed_tags": list(policy.allowed_tags),
            "blocked_unrated": sorted(policy.blocked_unrated),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_policy(stored: str) -> MediaPolicy:
    """Rebuild a stored policy; raises `ValueError` for anything this module never wrote."""

    value = json.loads(stored)
    policy = MediaPolicy(
        all_libraries=value["all_libraries"],
        library_ids=frozenset(value["library_ids"]),
        max_parental_rating=value["max_parental_rating"],
        max_parental_sub_rating=value["max_parental_sub_rating"],
        blocked_tags=tuple(value["blocked_tags"]),
        allowed_tags=tuple(value["allowed_tags"]),
        blocked_unrated=frozenset(value["blocked_unrated"]),
    )
    if encode_policy(policy) != stored or type(policy.all_libraries) is not bool:
        raise ValueError("Stored media policy is not canonical")
    return policy


def _records():
    return (
        select(UserRow, UserProviderIdentityRow, JellyfinAccountSyncRow)
        .join(UserProviderIdentityRow, UserProviderIdentityRow.user_id == UserRow.id)
        .outerjoin(JellyfinAccountSyncRow, JellyfinAccountSyncRow.user_id == UserRow.id)
    )


def _disable_cause(user: UserRow) -> DisableCause | None:
    if user.status == AccountStatus.ACTIVE.value:
        return None
    # A disable recorded without a cause is treated as local, so Jellyfin never lifts it.
    return DisableCause(user.disable_cause or DisableCause.LOCAL)


def _record(row) -> LinkRecord:
    user, identity, sync = row.UserRow, row.UserProviderIdentityRow, row.JellyfinAccountSyncRow
    return LinkRecord(
        account=LinkedAccount(
            user_id=user.id,
            role=Role(user.role),
            disable_cause=_disable_cause(user),
            last_verified_at=utc(identity.last_verified_at),
        ),
        identity=ProviderIdentity(
            JellyfinServerId(identity.server_id), JellyfinUserId(identity.provider_user_id)
        ),
        link_state=ProviderLinkState(identity.link_state),
        upstream=UpstreamState(sync.upstream_state) if sync is not None else None,
        name=sync.jellyfin_name if sync is not None else None,
        avatar_tag=sync.avatar_tag if sync is not None else None,
        generation=(
            ReadGeneration(identity.observed_generation)
            if identity.observed_generation is not None
            else None
        ),
    )


def linked_accounts(db: Session) -> list[LinkRecord]:
    """Every linked account in ID order."""

    return [_record(row) for row in db.execute(_records().order_by(UserRow.id)).all()]


def read_link(db: Session, user_id: int) -> LinkRecord | None:
    row = db.execute(_records().where(UserRow.id == user_id)).first()
    return _record(row) if row is not None else None


def find_identity(db: Session, identity: ProviderIdentity) -> int | None:
    """The account linked to exactly this server and user ID, never matched by name."""

    return db.scalar(
        select(UserProviderIdentityRow.user_id).where(
            UserProviderIdentityRow.server_id == identity.server_id,
            UserProviderIdentityRow.provider_user_id == identity.user_id.value,
        )
    )


def stored_snapshot(db: Session, user_id: int) -> JellyfinUserSnapshot | None:
    """The account's last verified Jellyfin snapshot, or None when none was ever stored.

    A stored value this module could not have written yields None, so it grants nothing.
    """

    row = db.execute(
        select(UserProviderIdentityRow, JellyfinAccountSyncRow)
        .join(
            JellyfinAccountSyncRow,
            JellyfinAccountSyncRow.user_id == UserProviderIdentityRow.user_id,
        )
        .where(UserProviderIdentityRow.user_id == user_id)
    ).first()
    if row is None:
        return None
    identity, sync = row.UserProviderIdentityRow, row.JellyfinAccountSyncRow
    try:
        return JellyfinUserSnapshot(
            identity=ProviderIdentity(
                JellyfinServerId(identity.server_id), JellyfinUserId(identity.provider_user_id)
            ),
            name=sync.jellyfin_name,
            avatar_tag=sync.avatar_tag,
            is_administrator=sync.is_administrator is True,
            is_disabled=sync.upstream_state != UpstreamState.ENABLED.value,
            media=decode_policy(sync.media_policy),
            verified_at=sync.verified_at.replace(tzinfo=UTC),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _write_sync(
    db: Session, user_id: int, change: Transition, now: datetime
) -> accounts.UsernameRefresh:
    """Record the attempt at `now`; a verified snapshot also counts as verified at `now`.

    A snapshot also refreshes the account's display name and username from Jellyfin.
    """

    attempt = {
        "last_attempt_at": stored_time(now),
        "last_outcome": change.outcome.value,
        "last_failure": change.failure,
        "updated_at": stored_time(now),
    }
    values = dict(attempt)
    if change.upstream is not None:
        values["upstream_state"] = change.upstream.value
    snapshot = change.snapshot
    if snapshot is not None:
        values |= {
            "jellyfin_name": snapshot.name,
            "is_administrator": snapshot.is_administrator,
            "media_policy": encode_policy(snapshot.media),
            "verified_at": stored_time(now),
        }
        db.execute(
            sqlite_insert(JellyfinAccountSyncRow)
            .values(user_id=user_id, avatar_tag=None, **values)
            .on_conflict_do_update(index_elements=[JellyfinAccountSyncRow.user_id], set_=values)
        )
        accounts.update_profile(
            db, user_id, {"display_name": snapshot.name[:DISPLAY_NAME_LIMIT]}, now=now
        )
        return accounts.adopt_jellyfin_name(db, user_id, snapshot.name, now=now)
    # Without a verified snapshot only an existing row records the attempt.
    db.execute(
        update(JellyfinAccountSyncRow)
        .where(JellyfinAccountSyncRow.user_id == user_id)
        .values(**values)
    )
    return accounts.UsernameRefresh.UNCHANGED


def _write_avatar(db: Session, user_id: int, change: AvatarChange, now: datetime) -> None:
    if change.avatar is None:
        accounts.remove_avatar(db, user_id)
    else:
        accounts.set_avatar(db, user_id, change.avatar, now=now)
    db.execute(
        update(JellyfinAccountSyncRow)
        .where(JellyfinAccountSyncRow.user_id == user_id)
        .values(avatar_tag=change.tag)
    )


def _event(user_id: int, now: datetime, details: dict) -> AuditEvent:
    return AuditEvent(
        now,
        None,
        None,
        AuditAction.JELLYFIN_ACCOUNT_STATE,
        AuditOutcome.SUCCEEDED,
        target_user=UserId(user_id),
        details=details,
    )


def _renamed(
    record: LinkRecord, change: Transition, username: accounts.UsernameRefresh
) -> accounts.UsernameRefresh | None:
    """The username result worth an event: a later rename, or a name first found held.

    The first snapshot of an account is covered by its create or link event, and a
    name still held at later reads is only logged, so each sync does not repeat it.
    """

    if username is accounts.UsernameRefresh.REFRESHED and record.name is not None:
        return username
    if username is accounts.UsernameRefresh.UNAVAILABLE:
        if change.snapshot.name != record.name:
            return username
        logger.info(
            "Jellyfin name of account %s is still held by another account", record.account.user_id
        )
    return None


def _apply_change(
    db: Session,
    record: LinkRecord,
    change: Transition,
    read: ReadStart,
    avatar: AvatarChange | None,
) -> SyncResult:
    user_id, now = record.account.user_id, read.at
    revised = change.link_state is not record.link_state
    identity = {
        "link_state": change.link_state.value,
        "observed_generation": read.generation,
        "updated_at": stored_time(now),
    }
    if change.snapshot is not None:
        identity["last_verified_at"] = stored_time(now)
    db.execute(
        update(UserProviderIdentityRow)
        .where(UserProviderIdentityRow.user_id == user_id)
        .values(**identity)
    )
    username = _write_sync(db, user_id, change, now)
    if avatar is not None and change.snapshot is not None:
        _write_avatar(db, user_id, avatar, now)
    if revised:
        db.execute(
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(permission_revision=UserRow.permission_revision + 1)
        )
    before = record.account.status
    after = AccountStatus.ACTIVE if change.disable_cause is None else AccountStatus.DISABLED
    restanding = change.disable_cause is not record.account.disable_cause
    ended = 0
    if restanding:
        ended = accounts.set_disable_cause(db, user_id, change.disable_cause, now=now)
    details: dict = {}
    if restanding or (
        change.link_state is ProviderLinkState.SERVER_REPLACED
        and record.link_state is not ProviderLinkState.SERVER_REPLACED
    ):
        details = {
            "disabled": restanding and after is AccountStatus.DISABLED,
            "enabled": before is AccountStatus.DISABLED and after is AccountStatus.ACTIVE,
            "sessions_revoked": ended,
        }
    renamed = _renamed(record, change, username)
    if renamed is not None:
        details["username"] = renamed.value
    if details:
        details["outcome"] = change.outcome.value
        stage_event(db, _event(user_id, now, details), actor_kind=ActorKind.SYSTEM)
    return SyncResult(
        user_id, change.outcome, change.link_state, record.account.role, after, change.failure
    )


def observe(
    db: Session,
    user_id: int,
    evidence: Evidence,
    read: ReadStart,
    avatar: AvatarChange | None = None,
    *,
    identity: ProviderIdentity | None = None,
) -> SyncResult | None:
    """Stage Jellyfin's answer about one account under the caller's write lock.

    `read` is what the read that produced `evidence` took before it began; its
    time is the attempt time and, for a snapshot, the verification time.
    Returns None, staging nothing, when the read was numbered before the last
    restore (another `reads.read_epoch`), the account or its link no longer exists,
    a read with an equal or higher generation was already applied, the read
    began before the clock was found stepped back and its answer would verify
    (`states.before_clock_step`), or `identity` names the identity the read asked
    about and the account has since been relinked to another (MU-35).
    """

    if read.epoch != read_epoch(db):
        return None
    record = read_link(db, user_id)
    if record is None or (record.generation is not None and read.generation <= record.generation):
        return None
    if identity is not None and record.identity != identity:
        return None
    change = transition(record.account, evidence, read.at)
    if read.generation < clock_fence(db):
        change = before_clock_step(change)
        if change is None:
            return None
    return _apply_change(db, record, change, read, avatar)


def apply(
    db: Session,
    user_id: int,
    evidence: Evidence,
    read: ReadStart,
    avatar: AvatarChange | None = None,
    *,
    cancelled: threading.Event | None = None,
    identity: ProviderIdentity | None = None,
) -> SyncResult | None:
    """`observe` in its own transaction; once `cancelled` is set, write nothing."""

    with write_lock(db):
        result = None
        if cancelled is None or not cancelled.is_set():
            result = observe(db, user_id, evidence, read, avatar, identity=identity)
        if result is None:
            db.rollback()
        else:
            db.commit()
    return result
