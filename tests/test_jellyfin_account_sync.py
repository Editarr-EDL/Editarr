"""MU-16 service tests: sync transitions, outage grace, and the shared Jellyfin helpers.

Jellyfin is the MU-08 `JellyfinBoundary` (fixture `editarr-jellyfin-users-1`)
over local HTTP, a labelled substitute for a live server. Every time comes from
a fixed clock; nothing sleeps.
"""

from __future__ import annotations

import json
import secrets
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from scanner.db import (
    ApiKeyRow,
    AuditEventRow,
    AuthSessionRow,
    JellyfinAccountSyncRow,
    JellyfinSettingsRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.persistence.rows.db_auth import UserProfileRow
from scanner.security.access import link_state
from scanner.security.access.capability_ids import Capability
from scanner.security.access.identity import CredentialKind, ProviderLinkState
from scanner.security.access.link_state import PROFILE_ONLY, VERIFICATION_GRACE
from scanner.security.api_key_crypto import load_api_key_wrap_key
from scanner.security.api_keys import rotate_api_key
from scanner.security.auth_repository_sessions import issue_session
from scanner.security.jellyfin_accounts import admission, reads, store
from scanner.security.jellyfin_accounts.admission import (
    SignInRefusal,
    SignInRefused,
)
from scanner.security.jellyfin_accounts.states import SyncOutcome
from scanner.security.jellyfin_accounts.sync import SyncRegistry, SyncTrigger
from tests.jellyfin_user_boundary import IDS, NOW, JellyfinBoundary
from tests.multi_user_access_support import OWNER, live_accounts

PASSWORDS = {user_id: f"pw-{name.lower()}-{user_id[:6]}" for name, user_id in IDS.items()}


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fixed = Clock()
    monkeypatch.setattr(link_state, "link_clock", fixed)
    return fixed


@pytest.fixture
def boundary():
    with JellyfinBoundary(secrets.token_urlsafe(24), PASSWORDS) as server:
        yield server


@pytest.fixture
def accounts(tmp_path, boundary):
    """`live_accounts` against the boundary. The owner's stand-in link is removed, so sync
    observes only the members a test links; a test that needs a linked owner links it."""
    live = live_accounts(tmp_path)
    with live.factory() as db:
        db.add(JellyfinSettingsRow(id=1, server_url=boundary.url, api_key=boundary.api_key))
        db.query(UserProviderIdentityRow).filter_by(user_id=OWNER).delete()
        db.commit()
    yield live
    live.close()


def _adapters(boundary, clock):
    def open_adapter(db):
        credentials = db.scalar(select(JellyfinSettingsRow.server_url))
        if credentials is None:
            return None
        return JellyfinUserAdapter(boundary.url, boundary.api_key, clock=clock)

    return open_adapter


def _import(accounts, boundary, clock, selected=None):
    with (
        accounts.factory() as db,
        JellyfinUserAdapter(boundary.url, boundary.api_key, clock=clock) as adapter,
    ):
        read = reads.begin_read(db, clock)
        listing = adapter.users()
        return admission.import_users(
            db,
            adapter,
            listing,
            selected,
            actor=OWNER,
            credential=CredentialKind.SESSION,
            read=read,
        )


def _sign_in(accounts, boundary, clock, name, password=None):
    with (
        accounts.factory() as db,
        JellyfinUserAdapter(boundary.url, boundary.api_key, clock=clock) as adapter,
    ):
        secret = PASSWORDS[IDS[name.title()]] if password is None else password
        return admission.sign_in(
            db,
            adapter,
            lambda: adapter.authenticate(name, secret),
            clock=clock,
            issue=lambda user_id: user_id,
        )


def _sync(accounts, boundary, clock, registry=None):
    with accounts.factory() as db:
        return (registry or SyncRegistry()).run(
            db, SyncTrigger.MANUAL, clock=clock, adapters=_adapters(boundary, clock)
        )


def _linked(accounts, name):
    with accounts.factory() as db:
        return db.scalar(
            select(UserProviderIdentityRow.user_id).where(
                UserProviderIdentityRow.provider_user_id == IDS[name]
            )
        )


def _row(accounts, row, user_id):
    with accounts.factory() as db:
        found = db.get(row, user_id)
        db.expunge_all()
        return found


def _count(accounts, *where):
    with accounts.factory() as db:
        return db.scalar(select(func.count()).select_from(UserProviderIdentityRow).where(*where))


def _sessions_and_keys(accounts, user_id):
    with accounts.factory() as db:
        session = issue_session(
            db, remember_me=False, user_id=user_id, configuration=accounts.configuration
        )
        rotate_api_key(db, user_id, load_api_key_wrap_key(accounts.configuration.data_dir))
    return session


def test_sync_applies_upstream_changes_and_keeps_data(accounts, boundary, clock):
    _import(accounts, boundary, clock)
    ids = {name: _linked(accounts, name) for name in ("Avery", "Blake", "Casey", "Drew", "Emery")}
    for user_id in ids.values():
        _sessions_and_keys(accounts, user_id)
    boundary.state = "changed"
    clock.now = NOW + timedelta(minutes=5)

    run = _sync(accounts, boundary, clock)

    outcomes = {result.user_id: result.outcome for result in run.results}
    assert outcomes == {
        ids["Avery"]: SyncOutcome.VERIFIED,
        ids["Blake"]: SyncOutcome.VERIFIED,
        ids["Casey"]: SyncOutcome.VERIFIED,
        ids["Drew"]: SyncOutcome.DISABLED,
        ids["Emery"]: SyncOutcome.DELETED,
    }
    # The fixture renames Blake's Jellyfin user to "Casey" and Casey's to "Casey Archive";
    # accounts follow the user ID, never the name.
    assert [
        (
            _row(accounts, UserRow, ids[name]).role,
            _row(accounts, UserProfileRow, ids[name]).display_name,
        )
        for name in ("Avery", "Blake", "Casey")
    ] == [("admin", "Avery"), ("user", "Casey"), ("user", "Casey Archive")]
    assert (
        _row(accounts, JellyfinAccountSyncRow, ids["Blake"]).avatar_tag == "a" * 4 + "0" * 27 + "2"
    )
    with accounts.factory() as db:
        for name in ("Drew", "Emery"):
            user_id = ids[name]
            assert db.get(UserRow, user_id).status == "disabled"
            assert (
                db.scalar(
                    select(func.count()).where(
                        AuthSessionRow.user_id == user_id, AuthSessionRow.revoked_at.is_(None)
                    )
                )
                == 0
            )
            assert db.scalar(select(ApiKeyRow.user_id).where(ApiKeyRow.user_id == user_id))
        assert db.get(JellyfinAccountSyncRow, ids["Emery"]).upstream_state == "deleted"
        assert db.get(UserRow, ids["Blake"]).status == "active"
        assert db.get(JellyfinAccountSyncRow, ids["Drew"]).upstream_state == "disabled"
        states = db.execute(
            select(AuditEventRow.target_user_id, AuditEventRow.details_json).where(
                AuditEventRow.action == "account.jellyfin.state"
            )
        ).all()
    disabled = sorted(user for user, details in states if '"disabled":true' in details)
    assert disabled == sorted(ids[name] for name in ("Drew", "Emery"))
    renamed = {
        user: json.loads(details)["username"] for user, details in states if "username" in details
    }
    assert renamed == {ids["Blake"]: "unavailable", ids["Casey"]: "refreshed"}
    assert run.counts() == {
        "verified": 3,
        "disabled": 1,
        "deleted": 1,
        "unverified": 0,
        "server_replaced": 0,
    }


def test_a_jellyfin_disable_follows_jellyfin_and_never_changes_a_role(accounts, boundary, clock):
    _import(accounts, boundary, clock)
    blake, drew = _linked(accounts, "Blake"), _linked(accounts, "Drew")
    boundary.state = "changed"
    _sync(accounts, boundary, clock)
    assert _row(accounts, UserRow, blake).role == "user"
    assert _row(accounts, UserRow, drew).status == "disabled"

    assert _row(accounts, UserRow, drew).disable_cause == "upstream_disabled"

    boundary.state = "baseline"
    _sync(accounts, boundary, clock)
    assert _row(accounts, JellyfinAccountSyncRow, drew).upstream_state == "enabled"
    assert (_row(accounts, UserRow, drew).status, _row(accounts, UserRow, blake).role) == (
        "active",
        "user",
    )
    with accounts.factory() as db:
        details = db.scalars(
            select(AuditEventRow.details_json)
            .where(AuditEventRow.action == "account.jellyfin.state")
            .where(AuditEventRow.target_user_id == drew)
            .order_by(AuditEventRow.id)
        ).all()
    assert [json.loads(row).get("enabled", False) for row in details] == [False, True]


def test_a_re_enabled_user_signs_in_again_and_a_deleted_one_never_returns(
    accounts, boundary, clock
):
    _import(accounts, boundary, clock)
    drew, emery = _linked(accounts, "Drew"), _linked(accounts, "Emery")
    boundary.state = "changed"
    _sync(accounts, boundary, clock)
    boundary.state = "baseline"

    assert _sign_in(accounts, boundary, clock, "Drew") == drew
    assert _row(accounts, UserRow, drew).disable_cause is None
    with pytest.raises(SignInRefused) as refused:
        _sign_in(accounts, boundary, clock, "Emery")
    assert (refused.value.refusal, refused.value.user_id) == (SignInRefusal.ACCOUNT_DISABLED, emery)
    _sync(accounts, boundary, clock)
    gone = _row(accounts, UserRow, emery)
    assert (gone.status, gone.disable_cause) == ("disabled", "upstream_deleted")
    assert _count(accounts, UserProviderIdentityRow.provider_user_id == IDS["Emery"]) == 1


def test_the_owner_keeps_full_access_through_every_upstream_change(accounts, boundary, clock):
    with accounts.factory() as db:
        db.add(
            UserProviderIdentityRow(
                user_id=OWNER,
                server_id=boundary.server_info()["Id"],
                provider_user_id=IDS["Drew"],
                link_state="verified",
                last_verified_at=NOW.replace(tzinfo=None),
            )
        )
        db.commit()
    for state, fault in (("changed", None), ("changed", "server-error"), ("replaced", None)):
        boundary.state, boundary.fault = state, fault
        clock.now += VERIFICATION_GRACE
        _sync(accounts, boundary, clock)
        owner = _row(accounts, UserRow, OWNER)
        assert (owner.role, owner.status) == ("owner", "active")
        assert Capability.ROLES_ASSIGN in accounts.access(OWNER).capabilities


def test_failed_refresh_keeps_last_success_and_grace_ends_at_exactly_24_hours(
    accounts, boundary, clock
):
    _import(accounts, boundary, clock, frozenset({IDS["Avery"]}))
    avery = _linked(accounts, "Avery")
    boundary.fault = "server-error"
    clock.now = NOW + timedelta(hours=1)

    run = _sync(accounts, boundary, clock)

    assert [result.outcome for result in run.results] == [SyncOutcome.UNVERIFIED]
    identity = _row(accounts, UserProviderIdentityRow, avery)
    assert (identity.link_state, identity.last_verified_at) == (
        "outage_grace",
        NOW.replace(tzinfo=None),
    )
    sync = _row(accounts, JellyfinAccountSyncRow, avery)
    assert (sync.verified_at, sync.last_failure) == (NOW.replace(tzinfo=None), "service_error")
    full = accounts.access(avery).capabilities
    assert Capability.ACCOUNTS_LIST in full

    clock.now = NOW + VERIFICATION_GRACE
    assert accounts.access(avery).capabilities == full
    assert accounts.access(avery).principal.account.link_state is ProviderLinkState.OUTAGE_GRACE
    clock.now = NOW + VERIFICATION_GRACE + timedelta(microseconds=1)
    expired = accounts.access(avery)
    assert expired.principal.account.link_state is ProviderLinkState.OUTAGE_EXPIRED
    assert expired.capabilities == PROFILE_ONLY & full

    _sync(accounts, boundary, clock)
    assert _row(accounts, UserProviderIdentityRow, avery).link_state == "outage_expired"
    boundary.fault = None
    _sync(accounts, boundary, clock)
    assert accounts.access(avery).capabilities == full
    assert _row(accounts, UserProviderIdentityRow, avery).last_verified_at == clock.now.replace(
        tzinfo=None
    )


def test_server_replacement_and_disconnection_keep_profile_access_only(accounts, boundary, clock):
    _import(accounts, boundary, clock, frozenset({IDS["Casey"]}))
    casey = _linked(accounts, "Casey")
    session = _sessions_and_keys(accounts, casey)
    boundary.state = "replaced"

    run = _sync(accounts, boundary, clock)

    assert [(r.outcome, r.failure) for r in run.results] == [
        (SyncOutcome.SERVER_REPLACED, "server_mismatch")
    ]
    access = accounts.access(casey, CredentialKind.SESSION, session.id)
    assert access.capabilities <= PROFILE_ONLY
    assert Capability.PROFILE_EDIT_OWN in access.capabilities
    assert _row(accounts, UserRow, casey).status == "active"
    assert _count(accounts) == 1

    boundary.state = "baseline"
    _sync(accounts, boundary, clock)
    assert _row(accounts, UserProviderIdentityRow, casey).link_state == "verified"
    with accounts.factory() as db:
        db.query(JellyfinSettingsRow).delete()
        db.commit()
    run = _sync(accounts, boundary, clock)
    assert [(r.outcome, r.failure) for r in run.results] == [
        (SyncOutcome.SERVER_REPLACED, "disconnected")
    ]


def test_stored_snapshot_restores_the_last_verified_policy(accounts, boundary, clock):
    _import(accounts, boundary, clock, frozenset({IDS["Casey"]}))
    casey = _linked(accounts, "Casey")
    with (
        accounts.factory() as db,
        JellyfinUserAdapter(boundary.url, boundary.api_key, clock=clock) as adapter,
    ):
        live = adapter.verify_user(store.read_link(db, casey).identity)
        assert store.stored_snapshot(db, casey) == live
        db.query(JellyfinAccountSyncRow).update({"media_policy": '{"all_libraries":1}'})
        db.commit()
        assert store.stored_snapshot(db, casey) is None
