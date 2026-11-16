"""Reusable MU-04 builders for capability decisions, fixture version `mu04-access-builders-3`.

`build_access` makes a `PrincipalAccess` for pure decision tables without a
database. `LiveAccounts` holds real accounts, sessions, and API keys in a
fresh migrated database and edits role defaults, overrides, status, and role
the way the storage contract requires: each change bumps
`permission_revision`. `tests/multi_user_access_http.py` serves them behind
the real access middleware.

The owner is linked to a verified synthetic Jellyfin identity, as setup leaves
it. The members are unlinked and active, so local media scopes keep deciding
their media; an upgrade would disable such accounts, and `link` gives one a
verified identity when a test needs it linked. Reauthentication checks a
Jellyfin password: `stand_in_jellyfin` makes Jellyfin's answer `OWNER_PASSWORD`
for the owner and `MEMBER_PASSWORD` for every member, without a server, unless
`link_jellyfin_user` linked the account to a real loopback server.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine, create_engine, delete, event, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import sessionmaker

import scanner.http.reauthentication as reauthentication
import scanner.security.access.fresh as fresh_module
from scanner.db import (
    RoleCapabilityDefaultRow,
    UserCapabilityOverrideRow,
    UserProviderIdentityRow,
    UserRow,
    initialize_sqlite_connection,
)
from scanner.formats.export_layout import JellyfinUserId, new_local_export_id
from scanner.integrations.jellyfin_user_snapshot import JellyfinUserSnapshot
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.persistence.repository.filter_preferences import start_preferences
from scanner.persistence.write_lock import write_lock
from scanner.security.access import link_state
from scanner.security.access.capability_ids import Capability
from scanner.security.access.catalog import CAPABILITIES
from scanner.security.access.fresh import FreshAuthentication, PasswordCheck
from scanner.security.access.grants import GrantInputs, PrincipalAccess, effective_capabilities
from scanner.security.access.identity import (
    Account,
    AccountStatus,
    CredentialKind,
    DisableCause,
    JellyfinServerId,
    PermissionRevision,
    Principal,
    ProviderIdentity,
    ProviderLinkState,
    UserId,
)
from scanner.security.access.load import load_access
from scanner.security.access.overrides import OverrideEffect, parse_override, parse_role_default
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDenial, MediaDenied
from scanner.security.access.targets import (
    INSTANCE,
    NO_MEDIA,
    AccountRef,
    MediaRef,
    ResourceRef,
    Target,
)
from scanner.security.access.vocabulary import Role
from scanner.security.access.vocabulary import Target as TargetKind
from scanner.security.api_key_crypto import load_api_key_wrap_key
from scanner.security.api_keys import rotate_api_key
from scanner.security.auth_repository import issue_session
from scanner.security.auth_repository_admin import adopt_unowned
from scanner.security.auth_repository_sessions import IssuedSession
from scanner.security.jellyfin_accounts import reads, store
from scanner.security.jellyfin_accounts.reauthentication import check_linked_password
from scanner.settings.config import Settings

BUILDERS_VERSION = "mu04-access-builders-3"
STAND_IN_SERVER = JellyfinServerId("live-accounts-jellyfin")
ORIGIN = {"Origin": "https://editarr.example"}
OWNER_PASSWORD = "owner password one"
MEMBER_PASSWORD = "member password two"
JELLYFIN_DOWN = "stand-in: Jellyfin does not answer"

OWNER, ADMIN, PEER_ADMIN, USER, PEER_USER = (UserId(n) for n in (1, 2, 3, 4, 5))
ROLE_OF: Mapping[UserId, Role] = {
    OWNER: Role.OWNER,
    ADMIN: Role.ADMIN,
    PEER_ADMIN: Role.ADMIN,
    USER: Role.USER,
    PEER_USER: Role.USER,
}
DEFAULT_ID: Mapping[Role, UserId] = {Role.OWNER: OWNER, Role.ADMIN: ADMIN, Role.USER: USER}

ALLOWED_MEDIA = MediaAllowed(MediaBasis.LOCAL_DEFAULT)
DENIED_MEDIA = MediaDenied(MediaDenial.NOT_IN_SCOPE)


def build_access(
    role: Role,
    *,
    user_id: UserId | None = None,
    credential: CredentialKind = CredentialKind.SESSION,
    session_id: int | None = None,
    status: AccountStatus = AccountStatus.ACTIVE,
    role_defaults: Mapping[Capability, bool] | None = None,
    overrides: Mapping[Capability, OverrideEffect] | None = None,
    unreadable: Iterable[Capability] = (),
    revision: int = 1,
) -> PrincipalAccess:
    """A principal whose capabilities come from the real evaluator, not from the test."""

    user_id = user_id if user_id is not None else DEFAULT_ID[role]
    if credential is CredentialKind.SESSION and session_id is None:
        session_id = 1000 + user_id
    owner = role is Role.OWNER
    linked = owner and status is AccountStatus.ACTIVE
    account = Account(
        user_id=user_id,
        role=role,
        status=status,
        local_export_id=new_local_export_id(),
        provider=stand_in_identity(user_id) if linked else None,
        link_state=ProviderLinkState.VERIFIED if linked else ProviderLinkState.UNLINKED,
    )
    inputs = GrantInputs(role, role_defaults or {}, overrides or {}, frozenset(unreadable))
    return PrincipalAccess(
        Principal(account, credential, PermissionRevision(revision)),
        effective_capabilities(inputs),
        session_id,
    )


def stand_in_identity(user_id: int) -> ProviderIdentity:
    """The synthetic Jellyfin identity `link` gives an account; no server holds it."""

    return ProviderIdentity(STAND_IN_SERVER, JellyfinUserId(f"{user_id:032x}"))


def account_ref(user_id: UserId) -> AccountRef:
    return AccountRef(user_id, ROLE_OF[user_id])


def self_ref(access: PrincipalAccess) -> AccountRef:
    return AccountRef(access.principal.user_id, access.principal.account.role)


def resource_of(owner: UserId | None, media=NO_MEDIA, *, visible: bool = False) -> ResourceRef:
    return ResourceRef(account_ref(owner) if owner is not None else None, media, visible)


def valid_target(capability: Capability, access: PrincipalAccess, media=ALLOWED_MEDIA) -> Target:
    """A target the capability can act on.

    Self and own-resource targets belong to the actor. Other-account and
    other-resource targets belong to an ordinary user other than the actor.
    """

    me = self_ref(access)
    other = account_ref(PEER_USER if access.principal.user_id != PEER_USER else USER)
    return {
        TargetKind.INSTANCE: INSTANCE,
        TargetKind.MEDIA: MediaRef(media),
        TargetKind.SELF: me,
        TargetKind.OWN_RESOURCE: ResourceRef(me, media),
        TargetKind.OTHER_RESOURCE: ResourceRef(other, media),
        TargetKind.OTHER_ACCOUNT: other,
        TargetKind.OTHER_ACCOUNT_RESOURCE: ResourceRef(other, media),
        TargetKind.SCOPED_RECORDS: ResourceRef(other, media),
    }[CAPABILITIES[capability].target]


def minted_proof(access: PrincipalAccess) -> FreshAuthentication:
    """Pure tables only: a genuine proof for this exact principal, without a database.

    HTTP and service tests mint proofs through `verify_reauthentication`.
    """

    return FreshAuthentication(access, fresh_module._SEAL)


@dataclass
class LiveAccounts:
    """Five real accounts (owner 1, admins 2 and 3, users 4 and 5) with sessions and keys."""

    path: Path
    configuration: Settings
    engine: Engine
    factory: sessionmaker
    sessions: dict[UserId, IssuedSession] = field(default_factory=dict)
    keys: dict[UserId, str] = field(default_factory=dict)

    def close(self) -> None:
        self.engine.dispose()

    def _write(self, *statements) -> None:
        with self.factory() as db:
            for statement in statements:
                db.execute(statement)
            db.commit()

    def _bump(self, *conditions):
        return (
            update(UserRow)
            .where(*conditions)
            .values(permission_revision=UserRow.permission_revision + 1)
        )

    def set_role_default(self, role: Role, capability: Capability, granted: bool | None) -> None:
        """Change a role default (None restores the catalog default) and bump that role's users."""

        if granted is None:
            change = delete(RoleCapabilityDefaultRow).where(
                RoleCapabilityDefaultRow.role == role.value,
                RoleCapabilityDefaultRow.capability == capability.value,
            )
        else:
            parsed = parse_role_default(role.value, capability.value, granted)
            values = {"granted": parsed.granted, "updated_at": datetime.now(timezone.utc)}
            change = (
                insert(RoleCapabilityDefaultRow)
                .values(role=role.value, capability=capability.value, **values)
                .on_conflict_do_update(index_elements=["role", "capability"], set_=values)
            )
        self._write(change, self._bump(UserRow.role == role.value))

    def set_override(self, user_id: UserId, capability: Capability, effect: OverrideEffect) -> None:
        """Store allow or deny, or delete the row for inherit, and bump the user's revision."""

        parsed = parse_override(capability.value, effect.value)
        if parsed.effect is OverrideEffect.INHERIT:
            change = delete(UserCapabilityOverrideRow).where(
                UserCapabilityOverrideRow.user_id == user_id,
                UserCapabilityOverrideRow.capability == capability.value,
            )
        else:
            values = {"effect": parsed.effect.value, "updated_at": datetime.now(timezone.utc)}
            change = (
                insert(UserCapabilityOverrideRow)
                .values(user_id=user_id, capability=capability.value, **values)
                .on_conflict_do_update(index_elements=["user_id", "capability"], set_=values)
            )
        self._write(change, self._bump(UserRow.id == user_id))

    def set_status(self, user_id: UserId, status: AccountStatus) -> None:
        cause = None if status is AccountStatus.ACTIVE else DisableCause.LOCAL.value
        self._write(
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(status=status.value, disable_cause=cause),
            self._bump(UserRow.id == user_id),
        )

    def link(self, user_id: UserId) -> ProviderIdentity:
        """Link the account to its verified stand-in identity, verified now."""

        identity = stand_in_identity(user_id)
        with self.factory() as db:
            _link(db, user_id, identity)
            db.execute(self._bump(UserRow.id == user_id))
            db.commit()
        return identity

    def unlink(self, user_id: UserId) -> None:
        self._write(
            delete(UserProviderIdentityRow).where(UserProviderIdentityRow.user_id == user_id),
            self._bump(UserRow.id == user_id),
        )

    def link_jellyfin_user(self, user_id: UserId, snapshot: JellyfinUserSnapshot) -> None:
        """Link the account to `snapshot`'s identity and store it verified now, as sync does.

        Reauthentication then asks the configured Jellyfin server under the snapshot's name.
        """

        with self.factory() as db:
            db.execute(
                delete(UserProviderIdentityRow).where(UserProviderIdentityRow.user_id == user_id)
            )
            _link(db, user_id, snapshot.identity)
            db.commit()
            read = reads.begin_read(db, link_state.link_clock)
            with write_lock(db):
                store.observe(db, user_id, snapshot, read)
                db.commit()

    def verify(self, db, user_id: int, password: str) -> PasswordCheck:
        """Jellyfin's answer to a reauthentication password.

        An account linked to a real (loopback) server asks it; any other account
        answers as the stand-in server would.
        """

        identity = db.get(UserProviderIdentityRow, user_id)
        if identity is not None and identity.server_id != STAND_IN_SERVER:
            return check_linked_password(db, user_id, password)
        if password == JELLYFIN_DOWN:
            return PasswordCheck.UNAVAILABLE
        accepted = password == (OWNER_PASSWORD if user_id == OWNER else MEMBER_PASSWORD)
        return PasswordCheck.ACCEPTED if accepted else PasswordCheck.REFUSED

    def stand_in_jellyfin(self, monkeypatch) -> None:
        """Answer reauthentication with `verify` instead of asking a Jellyfin server."""

        monkeypatch.setattr(reauthentication, "check_linked_password", self.verify)

    def set_role(self, user_id: UserId, role: Role) -> None:
        self._write(
            update(UserRow).where(UserRow.id == user_id).values(role=role.value),
            self._bump(UserRow.id == user_id),
        )

    def access(
        self,
        user_id: UserId,
        credential: CredentialKind = CredentialKind.SESSION,
        session_id: int | None = None,
    ) -> PrincipalAccess | None:
        with self.factory() as db:
            return load_access(db, user_id, credential, session_id)

    def cookie(self, user_id: UserId) -> dict[str, str]:
        return {**ORIGIN, "Cookie": f"editarr_session={self.sessions[user_id].token}"}

    def unsafe_cookie(self, user_id: UserId) -> dict[str, str]:
        return {**self.cookie(user_id), "X-CSRF-Token": self.sessions[user_id].csrf_token}

    def key(self, user_id: UserId) -> dict[str, str]:
        return {"X-Api-Key": self.keys[user_id]}


def _link(db, user_id: int, identity: ProviderIdentity) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        UserProviderIdentityRow(
            user_id=user_id,
            server_id=identity.server_id,
            provider_user_id=identity.user_id.value,
            link_state=ProviderLinkState.VERIFIED.value,
            last_verified_at=now,
            created_at=now,
            updated_at=now,
        )
    )


def add_linked_owner(db, username: str = "Owner") -> int:
    """Commit an owner linked to its stand-in Jellyfin identity, as setup leaves one."""

    owner = UserRow(
        role=Role.OWNER.value,
        status=AccountStatus.ACTIVE.value,
        username=username,
        normalized_username=username.casefold(),
        local_export_id=new_local_export_id().value,
    )
    db.add(owner)
    db.flush()
    start_preferences(db, owner.id, datetime.now(timezone.utc))
    _link(db, owner.id, stand_in_identity(owner.id))
    adopt_unowned(db, owner.id)
    db.commit()
    return owner.id


def live_accounts(tmp_path: Path) -> LiveAccounts:
    """Migrate a fresh database, add the linked owner and four members, and sign in all."""

    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    event.listen(engine, "connect", initialize_sqlite_connection)
    factory = sessionmaker(bind=engine)
    configuration = Settings(data_dir=tmp_path)
    accounts = LiveAccounts(path, configuration, engine, factory)
    with factory() as db:
        for user_id, role in ROLE_OF.items():
            name = "Owner" if user_id == OWNER else f"member{user_id}"
            db.add(
                UserRow(
                    id=user_id,
                    role=role.value,
                    status="active",
                    username=name,
                    normalized_username=name.casefold(),
                    local_export_id=new_local_export_id().value,
                )
            )
        db.flush()
        for user_id in ROLE_OF:
            start_preferences(db, user_id, datetime.now(timezone.utc))
        _link(db, OWNER, stand_in_identity(OWNER))
        adopt_unowned(db, OWNER)
        db.commit()
        for user_id in ROLE_OF:
            accounts.sessions[user_id] = issue_session(
                db, remember_me=False, user_id=user_id, configuration=configuration
            )
            accounts.keys[user_id] = rotate_api_key(
                db, user_id, load_api_key_wrap_key(configuration.data_dir)
            )
    return accounts
