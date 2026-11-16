"""MU-16: Jellyfin reads are ordered by a stored generation, never by the wall clock.

Each read takes the next generation from the database before Jellyfin is
asked; an account keeps the answer of its highest-generation read. The wall
clock only times verification and grace, and a clock that steps back ends
every stored verification instead of lengthening it. Jellyfin is the MU-08
`JellyfinBoundary` (`editarr-jellyfin-users-1`); clocks are fixed and stepped.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from scanner.db import UserProviderIdentityRow, UserRow
from scanner.integrations.jellyfin_user_snapshot import Unverified, UnverifiedReason
from scanner.persistence.rows.db_auth import AccountSettingsRow
from scanner.security.access.identity import CredentialKind, ProviderLinkState, UserId
from scanner.security.access.link_state import (
    VERIFICATION_GRACE,
    effective_link_state,
    elapsed_within,
)
from scanner.security.access.load import load_access
from scanner.security.access.vocabulary import Role
from scanner.security.jellyfin_accounts import reads, store
from scanner.security.jellyfin_accounts.evidence import EvidenceCache
from scanner.security.jellyfin_accounts.states import LinkedAccount, transition
from tests import test_jellyfin_account_evidence as evidence_support
from tests import test_jellyfin_account_sync as support
from tests.jellyfin_user_boundary import IDS, NOW, PRIMARY
from tests.media_access_support import jellyfin_user
from tests.test_jellyfin_account_sync import accounts as accounts
from tests.test_jellyfin_account_sync import boundary as boundary
from tests.test_jellyfin_account_sync import clock as clock

OUTAGE = Unverified(UnverifiedReason.UNREACHABLE)


def _counter(accounts):
    with accounts.factory() as db:
        return db.execute(
            select(AccountSettingsRow.jellyfin_read_generation, AccountSettingsRow.jellyfin_read_at)
        ).one_or_none()


def _state(db, user_id, now):
    access = load_access(db, UserId(user_id), CredentialKind.API_KEY, now=now)
    return access.principal.account.link_state


def test_generations_increase_on_a_fixed_clock_and_across_a_restart(accounts, clock):
    with accounts.factory() as db:
        first = reads.begin_read(db, clock)
        second = reads.begin_read(db, clock)
    accounts.close()
    restarted = create_engine(f"sqlite:///{accounts.path}")
    try:
        with sessionmaker(bind=restarted)() as db:
            third = reads.begin_read(db, clock)
    finally:
        restarted.dispose()

    assert first.generation < second.generation < third.generation
    assert first.at == second.at == third.at == NOW
    assert _counter(accounts) == (third.generation, NOW.replace(tzinfo=None))


def test_the_later_read_wins_even_when_the_clock_stepped_back_between_them(
    accounts, boundary, clock
):
    support._import(accounts, boundary, clock, frozenset({IDS["Casey"]}))
    user_id = support._linked(accounts, "Casey")
    with accounts.factory() as db:
        older = reads.begin_read(db, clock)
        clock.now -= timedelta(seconds=1)
        newer = reads.begin_read(db, clock)
        disabled = replace(jellyfin_user("Casey", verified_at=newer.at), is_disabled=True)
        assert store.apply(db, user_id, disabled, newer) is not None
        enabled = jellyfin_user("Casey", verified_at=older.at)
        assert store.apply(db, user_id, enabled, older) is None
        assert store.apply(db, user_id, enabled, newer) is None

    assert support._row(accounts, UserRow, user_id).status == "disabled"
    link = support._row(accounts, UserProviderIdentityRow, user_id)
    assert link.observed_generation == newer.generation


def test_an_earlier_read_applied_first_still_lets_the_later_one_apply(accounts, boundary, clock):
    support._import(accounts, boundary, clock, frozenset({IDS["Casey"]}))
    user_id = support._linked(accounts, "Casey")
    with accounts.factory() as db:
        older = reads.begin_read(db, clock)
        newer = reads.begin_read(db, clock)
        assert store.apply(db, user_id, jellyfin_user("Casey", verified_at=NOW), older)
        disabled = replace(jellyfin_user("Casey", verified_at=NOW), is_disabled=True)
        assert store.apply(db, user_id, disabled, newer) is not None

    assert support._row(accounts, UserRow, user_id).status == "disabled"


def test_a_clock_step_back_ends_every_stored_verification(accounts, boundary, clock):
    support._import(accounts, boundary, clock)
    linked = [support._linked(accounts, name) for name in ("Avery", "Casey")]
    with accounts.factory() as db:
        assert reads.begin_read(db, clock).at == NOW
        assert {_state(db, user_id, NOW) for user_id in linked} == {ProviderLinkState.VERIFIED}
        clock.now = NOW - timedelta(minutes=1)
        reads.begin_read(db, clock)
        later = NOW + timedelta(minutes=1)
        expired = {_state(db, user_id, later) for user_id in linked}
        assert expired == {ProviderLinkState.OUTAGE_EXPIRED}
    for user_id in linked:
        assert support._row(accounts, UserProviderIdentityRow, user_id).last_verified_at is None


def test_access_fails_closed_until_a_read_notices_the_clock_stepped_back(accounts, boundary, clock):
    support._import(accounts, boundary, clock, frozenset({IDS["Casey"]}))
    user_id = support._linked(accounts, "Casey")
    with accounts.factory() as db:
        clock.now = NOW + timedelta(hours=2)
        reads.begin_read(db, clock)
        assert _state(db, user_id, NOW + timedelta(hours=2)) is ProviderLinkState.VERIFIED
        assert _state(db, user_id, NOW + timedelta(hours=1)) is ProviderLinkState.OUTAGE_EXPIRED


def test_a_cancelled_read_takes_no_generation(accounts, clock):
    with accounts.factory() as db:
        before = reads.begin_read(db, clock)
        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(reads.ReadCancelled):
            reads.begin_read(db, clock, cancelled=cancelled)
    assert _counter(accounts)[0] == before.generation


@pytest.mark.parametrize(
    ("since", "within"),
    [
        (NOW - VERIFICATION_GRACE, True),
        (NOW - VERIFICATION_GRACE - timedelta(microseconds=1), False),
        (NOW, True),
        (NOW + timedelta(microseconds=1), False),
        (None, False),
    ],
)
def test_a_verification_after_now_is_never_within_grace(since, within):
    assert elapsed_within(since, NOW) is within
    expected = ProviderLinkState.VERIFIED if within else ProviderLinkState.OUTAGE_EXPIRED
    assert effective_link_state(ProviderLinkState.VERIFIED, since, NOW, last_read_at=None) is (
        expected
    )
    account = LinkedAccount(5, Role.USER, None, since)
    grace = ProviderLinkState.OUTAGE_GRACE if within else ProviderLinkState.OUTAGE_EXPIRED
    assert transition(account, OUTAGE, NOW).link_state is grace


def test_the_evidence_cache_never_falls_back_to_a_copy_timed_after_now():
    cache = EvidenceCache()
    catalog = evidence_support.ScriptedAdapter(lambda: NOW).catalog(PRIMARY)
    assert cache._read("catalog", PRIMARY, lambda _server: catalog, NOW) is catalog
    outage = Unverified(UnverifiedReason.INVALID_RESPONSE)
    stepped_back = NOW - timedelta(seconds=1)
    assert cache._read("catalog", PRIMARY, lambda _server: outage, stepped_back) is outage
    assert cache._read("catalog", PRIMARY, lambda _server: outage, NOW) is catalog
