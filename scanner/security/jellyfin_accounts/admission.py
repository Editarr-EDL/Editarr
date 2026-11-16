"""Create linked accounts at first Jellyfin sign-in and by explicit import.

Identities are matched by linked server and Jellyfin user ID only. Creation
holds the SQLite write lock across the lookup and the insert, so overlapping
sign-ins and imports for one Jellyfin user create one account. Network reads
(sign-in, user list, avatar) happen before the lock, each after taking its read
generation (`reads.begin_read`), and each answer is stored by `store.observe`,
so an answer from a read older than one already applied changes nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.formats.export_layout import new_local_export_id
from scanner.integrations.jellyfin_user_snapshot import (
    CredentialProof,
    JellyfinUserSnapshot,
    Unverified,
    UnverifiedReason,
    UserAvatar,
)
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.persistence.repository.filter_preferences import start_preferences
from scanner.persistence.rows.db_auth import UserProviderIdentityRow, UserRow
from scanner.persistence.write_lock import write_lock
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.decisions import admission_error
from scanner.security.access.identity import (
    AccountStatus,
    CredentialKind,
    ProviderLinkState,
    UserId,
)
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.access.vocabulary import Role
from scanner.security.account_avatars import AvatarRejected, decode_avatar
from scanner.security.audit.actions import ActorKind, AuditAction
from scanner.security.audit.store import stage_event

from . import blocks, reads, store
from .reads import ReadStart
from .states import starting_role

T = TypeVar("T")


class SignInRefusal(StrEnum):
    INVALID_CREDENTIALS = "invalid_credentials"
    JELLYFIN_UNAVAILABLE = "jellyfin_unavailable"
    ACCOUNT_DISABLED = AccessErrorCode.ACCOUNT_DISABLED.value
    JELLYFIN_USER_BLOCKED = "jellyfin_user_blocked"


class SignInRefused(Exception):
    """No session: `refusal` says why, and `user_id` names a known account if any."""

    def __init__(self, refusal: SignInRefusal, user_id: int | None = None) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal
        self.user_id = user_id


class ListingSuperseded(Exception):
    """The clock was found stepped back or the database restored after the listing was read."""


class ImportOutcome(StrEnum):
    IMPORTED = "imported"
    EXISTING = "existing"
    UPSTREAM_DISABLED = "upstream_disabled"
    BLOCKED = "blocked"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ImportResult:
    jellyfin_user_id: str
    name: str | None
    outcome: ImportOutcome
    user_id: int | None


def _avatar(fetched: UserAvatar | None | Unverified, tag: str | None) -> store.AvatarChange | None:
    if isinstance(fetched, Unverified):
        return None
    if fetched is None:
        return store.AvatarChange(tag, None)
    try:
        return store.AvatarChange(tag, decode_avatar(fetched.content))
    except AvatarRejected:
        # An image Editarr cannot re-encode shows no avatar until Jellyfin's tag changes.
        return store.AvatarChange(tag, None)


def fetch_avatar(
    adapter: JellyfinUserAdapter, snapshot: JellyfinUserSnapshot, stored_tag: str | None
) -> store.AvatarChange | None:
    """Read the user's Jellyfin avatar when its tag differs from the stored one."""

    if snapshot.avatar_tag == stored_tag:
        return None
    if snapshot.avatar_tag is None:
        return store.AvatarChange(None, None)
    return _avatar(adapter.avatar(snapshot.identity, snapshot.avatar_tag), snapshot.avatar_tag)


def create_linked(
    db: Session, snapshot: JellyfinUserSnapshot, now: datetime, role: Role | None = None
) -> int:
    """Stage an account linked to `snapshot`'s identity, with its starting filter mode.

    `role` defaults to the snapshot's starting role.
    """
    stored = store.stored_time(now)
    user = UserRow(
        role=(role or starting_role(snapshot)).value,
        status=AccountStatus.ACTIVE.value,
        local_export_id=new_local_export_id().value,
        created_at=stored,
        updated_at=stored,
    )
    db.add(user)
    db.flush()
    db.add(
        UserProviderIdentityRow(
            user_id=user.id,
            server_id=snapshot.identity.server_id,
            provider_user_id=snapshot.identity.user_id.value,
            link_state=ProviderLinkState.VERIFIED.value,
            created_at=stored,
            updated_at=stored,
        )
    )
    start_preferences(db, user.id, stored)
    db.flush()
    return user.id


def _admit(
    db: Session,
    snapshot: JellyfinUserSnapshot,
    read: ReadStart,
    avatar: store.AvatarChange | None,
) -> tuple[int, bool, bool]:
    """Apply a verified snapshot to its linked account, creating one if none exists.

    Runs under the caller's write lock. Returns the account, whether it is new,
    and whether the snapshot applied: a newer read leaves the account alone.
    """

    user_id = store.find_identity(db, snapshot.identity)
    created = user_id is None
    if created:
        user_id = create_linked(db, snapshot, read.at)
    applied = store.observe(db, user_id, snapshot, read, avatar) is not None
    return user_id, created, applied


def _created_event(
    user_id: int,
    now: datetime,
    method: str,
    actor: UserId | None = None,
    credential: CredentialKind | None = None,
) -> AuditEvent:
    return AuditEvent(
        now,
        actor,
        credential,
        AuditAction.ACCOUNT_CREATE,
        AuditOutcome.SUCCEEDED,
        target_user=UserId(user_id),
        details={"method": method},
    )


def _blocked_import(now: datetime, actor: UserId, credential: CredentialKind) -> AuditEvent:
    return AuditEvent(
        now,
        actor,
        credential,
        AuditAction.ACCOUNT_CREATE,
        AuditOutcome.DENIED,
        details={"method": "jellyfin_import", "reason": SignInRefusal.JELLYFIN_USER_BLOCKED.value},
    )


def encodable(*values: str) -> bool:
    """Whether every value encodes as UTF-8; lone surrogates never reach Jellyfin."""

    try:
        for value in values:
            value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def sign_in(
    db: Session,
    adapter: JellyfinUserAdapter,
    prove: Callable[[], CredentialProof | Unverified],
    *,
    clock: Callable[[], datetime],
    issue: Callable[[int], T],
) -> T:
    """Prove a Jellyfin user with `prove`, sync or create the linked account, and admit it.

    `prove` asks Jellyfin once, with a password or an approved Quick Connect
    secret. The sign-in takes its read generation and time from `clock` before Jellyfin
    is asked. `issue` stages the session for the admitted account in the
    transaction that admits it, so no disable commits between them; its result
    is returned. Raises `SignInRefused` otherwise, including
    `JELLYFIN_UNAVAILABLE` when a later Jellyfin read already changed the
    account, found the clock stepped back, or a restore replaced the database.
    Jellyfin's own sign-in session is accepted whether or not its logout was confirmed
    (`SessionCleanup.MAY_REMAIN`): the credentials were proven, and Jellyfin
    replaces that session at Editarr's next sign-in.
    """

    read = reads.begin_read(db, clock)
    proof = prove()
    if isinstance(proof, Unverified):
        if proof.reason is UnverifiedReason.CREDENTIALS_REJECTED:
            raise SignInRefused(SignInRefusal.INVALID_CREDENTIALS)
        raise SignInRefused(SignInRefusal.JELLYFIN_UNAVAILABLE)
    snapshot = proof.user
    existing = store.find_identity(db, snapshot.identity)
    known = store.read_link(db, existing) if existing is not None else None
    avatar = fetch_avatar(adapter, snapshot, known.avatar_tag if known else None)
    db.rollback()
    with write_lock(db):
        user_id = store.find_identity(db, snapshot.identity)
        if blocks.is_blocked(db, snapshot.identity):
            raise SignInRefused(SignInRefusal.JELLYFIN_USER_BLOCKED)
        if user_id is None and snapshot.is_disabled:
            raise SignInRefused(SignInRefusal.ACCOUNT_DISABLED)
        user_id, created, applied = _admit(db, snapshot, read, avatar)
        if not applied:
            raise SignInRefused(SignInRefusal.JELLYFIN_UNAVAILABLE, None if created else user_id)
        if created:
            stage_event(
                db,
                _created_event(user_id, read.at, "jellyfin_first_login"),
                actor_kind=ActorKind.ANONYMOUS,
            )
        status = db.execute(select(UserRow.status).where(UserRow.id == user_id)).scalar_one()
        denial = admission_error(AccountStatus(status))
        issued = issue(user_id) if denial is None else None
        db.commit()
    if denial is not None:
        raise SignInRefused(SignInRefusal(denial.value), user_id)
    return issued


def import_users(
    db: Session,
    adapter: JellyfinUserAdapter,
    listing: tuple[JellyfinUserSnapshot, ...],
    selected: frozenset[str] | None,
    *,
    actor: UserId,
    credential: CredentialKind,
    read: ReadStart,
) -> list[ImportResult]:
    """Create accounts for the selected listed users, or every listed user when `selected` is None.

    `read` is what the listing read took before it began. An already linked
    user is refreshed from the listing, unless a later read already changed it,
    and reported `existing`. A user Jellyfin lists as disabled is not imported. A
    selected ID absent from the listing is `not_found`; absence is never
    treated as a deletion here. Raises `ListingSuperseded`, creating nothing
    more, when a read numbered after the listing found the clock stepped back or a
    restore replaced the database after it.
    """

    listed = {snapshot.identity.user_id.value: snapshot for snapshot in listing}
    wanted = sorted(listed) if selected is None else sorted(selected)
    results = []
    for jellyfin_id in wanted:
        snapshot = listed.get(jellyfin_id)
        if snapshot is None:
            results.append(ImportResult(jellyfin_id, None, ImportOutcome.NOT_FOUND, None))
            continue
        if snapshot.is_disabled and store.find_identity(db, snapshot.identity) is None:
            results.append(
                ImportResult(jellyfin_id, snapshot.name, ImportOutcome.UPSTREAM_DISABLED, None)
            )
            continue
        existing = store.find_identity(db, snapshot.identity)
        known = store.read_link(db, existing) if existing is not None else None
        avatar = fetch_avatar(adapter, snapshot, known.avatar_tag if known else None)
        db.rollback()
        with write_lock(db):
            if blocks.is_blocked(db, snapshot.identity):
                stage_event(db, _blocked_import(read.at, actor, credential))
                db.commit()
                results.append(
                    ImportResult(jellyfin_id, snapshot.name, ImportOutcome.BLOCKED, None)
                )
                continue
            user_id, created, applied = _admit(db, snapshot, read, avatar)
            if created and not applied:
                raise ListingSuperseded
            if created:
                stage_event(
                    db, _created_event(user_id, read.at, "jellyfin_import", actor, credential)
                )
            db.commit()
        outcome = ImportOutcome.IMPORTED if created else ImportOutcome.EXISTING
        results.append(ImportResult(jellyfin_id, snapshot.name, outcome, user_id))
    return results
