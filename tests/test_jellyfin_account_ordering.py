"""MU-16: Jellyfin answers apply in read order; the sync runs on schedule and drains on stop.

An account keeps the newest Jellyfin observation applied to it, judged by when
each read began. An older one (a slow sign-in, a retained listing, a delayed
answer of any kind) changes nothing. Jellyfin is the MU-08 `JellyfinBoundary`
(`editarr-jellyfin-users-1`); clocks are fixed and threads meet at events.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import timedelta

import pytest

from scanner.db import JellyfinAccountSyncRow, UserProviderIdentityRow, UserRow
from scanner.http import api_lifecycle
from scanner.integrations.jellyfin_user_snapshot import Unverified, UnverifiedReason, UserDeleted
from scanner.runtime.jellyfin_sync_scheduler import JellyfinSyncScheduler
from scanner.security.access.identity import CredentialKind
from scanner.security.jellyfin_accounts import admission, reads, store
from scanner.security.jellyfin_accounts import sync as account_sync
from scanner.security.jellyfin_accounts.admission import (
    ImportOutcome,
    SignInRefusal,
    SignInRefused,
)
from scanner.security.jellyfin_accounts.sync import SyncInProgress, SyncRegistry, SyncTrigger
from tests import test_jellyfin_account_sync as support
from tests.jellyfin_user_boundary import IDS, NOW, identity
from tests.media_access_support import jellyfin_user
from tests.multi_user_access_support import OWNER
from tests.test_jellyfin_account_sync import accounts as accounts
from tests.test_jellyfin_account_sync import boundary as boundary
from tests.test_jellyfin_account_sync import clock as clock

LATER = NOW + timedelta(minutes=2)


def _naive(moment):
    return moment.replace(tzinfo=None)


@pytest.mark.parametrize(
    ("name", "confirmed"), [("Drew", ("disabled", False)), ("Avery", ("active", False))]
)
def test_a_sign_in_read_before_a_newer_sync_is_refused_and_changes_nothing(
    accounts, boundary, clock, monkeypatch, name, confirmed
):
    support._import(accounts, boundary, clock, frozenset({IDS[name]}))
    user_id = support._linked(accounts, name)
    read, release = threading.Event(), threading.Event()
    original = admission.fetch_avatar

    def held(*args):
        read.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(admission, "fetch_avatar", held)
    outcome = []

    def sign_in():
        try:
            outcome.append(support._sign_in(accounts, boundary, clock, name))
        except SignInRefused as refused:
            outcome.append(refused.refusal)

    clock.now = NOW + timedelta(minutes=1)
    worker = threading.Thread(target=sign_in)
    worker.start()
    try:
        assert read.wait(10)
        clock.now = LATER
        boundary.state = "changed"
        support._sync(accounts, boundary, clock)
    finally:
        release.set()
        worker.join(10)

    assert outcome == [SignInRefusal.JELLYFIN_UNAVAILABLE]
    user = support._row(accounts, UserRow, user_id)
    link = support._row(accounts, UserProviderIdentityRow, user_id)
    administrator = support._row(accounts, JellyfinAccountSyncRow, user_id).is_administrator
    assert (user.status, administrator) == confirmed
    assert link.last_verified_at == _naive(LATER)


def test_an_import_of_an_older_listing_leaves_a_newer_sync_in_place(accounts, boundary, clock):
    support._import(accounts, boundary, clock, frozenset({IDS["Avery"]}))
    user_id = support._linked(accounts, "Avery")
    with (
        accounts.factory() as db,
        support.JellyfinUserAdapter(boundary.url, boundary.api_key, clock=clock) as adapter,
    ):
        listing = reads.begin_read(db, clock)
        older = adapter.users()
        clock.now = LATER
        boundary.state = "changed"
        support._sync(accounts, boundary, clock)
        results = admission.import_users(
            db,
            adapter,
            older,
            frozenset({IDS["Avery"]}),
            actor=OWNER,
            credential=CredentialKind.SESSION,
            read=listing,
        )

    assert [result.outcome for result in results] == [ImportOutcome.EXISTING]
    assert support._row(accounts, JellyfinAccountSyncRow, user_id).is_administrator is False
    link = support._row(accounts, UserProviderIdentityRow, user_id)
    assert link.last_verified_at == _naive(LATER)
    assert link.observed_generation > listing.generation


ENABLED = jellyfin_user("Casey", verified_at=NOW)
DISABLED = replace(jellyfin_user("Casey", verified_at=LATER), is_disabled=True)
DELETED = UserDeleted(identity("Casey"), LATER)
REPLACED = Unverified(UnverifiedReason.SERVER_MISMATCH)
OUTAGE = Unverified(UnverifiedReason.UNREACHABLE)


@pytest.mark.parametrize(
    ("newer", "older", "kept"),
    [
        (DISABLED, ENABLED, ("disabled", "verified", "disabled")),
        (DELETED, ENABLED, ("disabled", "verified", "deleted")),
        (REPLACED, ENABLED, ("active", "server_replaced", "server_replaced")),
        (jellyfin_user("Casey", verified_at=LATER), OUTAGE, ("active", "verified", "verified")),
    ],
    ids=["disable", "deletion", "replacement", "success"],
)
def test_an_older_answer_of_any_kind_never_replaces_a_newer_one(
    accounts, boundary, clock, newer, older, kept
):
    support._import(accounts, boundary, clock, frozenset({IDS["Casey"]}))
    user_id = support._linked(accounts, "Casey")
    with accounts.factory() as db:
        older_read = reads.begin_read(db, lambda: LATER - timedelta(microseconds=1))
        newer_read = reads.begin_read(db, lambda: LATER)
        assert store.apply(db, user_id, newer, newer_read) is not None
        assert store.apply(db, user_id, older, older_read) is None

    user = support._row(accounts, UserRow, user_id)
    link = support._row(accounts, UserProviderIdentityRow, user_id)
    sync = support._row(accounts, JellyfinAccountSyncRow, user_id)
    assert (user.status, link.link_state, sync.last_outcome) == kept
    assert link.observed_generation == newer_read.generation
    assert sync.last_attempt_at == _naive(LATER)


class HeldAdapter:
    """A Jellyfin read that waits for `release`, then reports an outage."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()
        self.reads = 0

    def verify_user(self, _identity):
        self.reads += 1
        self.entered.set()
        assert self.release.wait(10)
        return OUTAGE

    def close(self) -> None:
        self.closed.set()


def _attempt(accounts, user_id):
    sync = support._row(accounts, JellyfinAccountSyncRow, user_id)
    return sync.last_attempt_at, sync.last_outcome


def test_shutdown_cancels_a_sync_waiting_on_jellyfin_and_returns_once_it_ends(
    accounts, boundary, clock
):
    support._import(accounts, boundary, clock, frozenset({IDS["Casey"], IDS["Avery"]}))
    linked = [support._linked(accounts, name) for name in ("Casey", "Avery")]
    before = [_attempt(accounts, user_id) for user_id in linked]
    held = HeldAdapter()
    clock.now = LATER
    scheduler = JellyfinSyncScheduler(
        accounts.factory, clock=clock, adapters=lambda _db: held, sync=SyncRegistry()
    )
    answer = threading.Thread(target=lambda: scheduler.stop_event.wait(10) and held.release.set())
    answer.start()
    scheduler.start()
    try:
        assert held.entered.wait(10)
        assert scheduler.stop() is True
        assert not scheduler.thread.is_alive() and held.closed.is_set()
    finally:
        held.release.set()
        scheduler.thread.join(10)
        answer.join(10)

    assert held.reads == 1
    assert [_attempt(accounts, user_id) for user_id in linked] == before


def test_a_cancel_arriving_just_before_the_write_still_writes_nothing(
    accounts, boundary, clock, monkeypatch
):
    support._import(accounts, boundary, clock, frozenset({IDS["Casey"]}))
    user_id = support._linked(accounts, "Casey")
    before = _attempt(accounts, user_id)
    cancelled = threading.Event()
    original = account_sync.fetch_avatar

    def cancel_then_fetch(*args):
        cancelled.set()
        return original(*args)

    monkeypatch.setattr(account_sync, "fetch_avatar", cancel_then_fetch)
    clock.now = LATER
    with accounts.factory() as db:
        run = SyncRegistry().run(
            db,
            SyncTrigger.SCHEDULED,
            clock=clock,
            adapters=support._adapters(boundary, clock),
            cancelled=cancelled,
        )

    assert run.results == ()
    assert _attempt(accounts, user_id) == before


def test_a_cancel_between_accounts_numbers_and_reads_no_further_account(
    accounts, boundary, clock, monkeypatch
):
    support._import(accounts, boundary, clock, frozenset({IDS["Casey"], IDS["Avery"]}))
    cancelled = threading.Event()
    reads_made = []
    original = store.apply

    class Counting:
        def verify_user(self, identity):
            reads_made.append(identity)
            return OUTAGE

        def close(self) -> None:
            pass

    def apply_then_cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        cancelled.set()
        return result

    monkeypatch.setattr(store, "apply", apply_then_cancel)
    with accounts.factory() as db:
        before = reads.begin_read(db, clock).generation
        run = SyncRegistry().run(
            db,
            SyncTrigger.SCHEDULED,
            clock=clock,
            adapters=lambda _db: Counting(),
            cancelled=cancelled,
        )
        after = reads.begin_read(db, clock).generation

    assert len(run.results) == len(reads_made) == 1
    assert after == before + 2


def test_a_sync_reading_past_the_drain_bound_is_reported_and_never_writes(
    accounts, boundary, clock
):
    support._import(accounts, boundary, clock, frozenset({IDS["Casey"]}))
    user_id = support._linked(accounts, "Casey")
    before = _attempt(accounts, user_id)
    held = HeldAdapter()
    clock.now = LATER
    scheduler = JellyfinSyncScheduler(
        accounts.factory, clock=clock, adapters=lambda _db: held, sync=SyncRegistry()
    )
    scheduler.start()
    try:
        assert held.entered.wait(10)
        assert scheduler.stop(timeout=0.05) is False
        assert scheduler.thread.is_alive() and not held.closed.is_set()
    finally:
        held.release.set()
        scheduler.thread.join(10)

    assert not scheduler.thread.is_alive() and held.closed.is_set()
    assert _attempt(accounts, user_id) == before


@pytest.mark.parametrize("ended", [True, False])
def test_application_shutdown_keeps_a_sync_scheduler_whose_run_has_not_ended(monkeypatch, ended):
    from scanner import api as root

    class Scheduler:
        def stop(self):
            return ended

    scheduler = Scheduler()
    monkeypatch.setattr(root.app.state, "backup_scheduler", None, raising=False)
    monkeypatch.setattr(root.app.state, "jellyfin_sync_scheduler", scheduler, raising=False)
    monkeypatch.setattr(root, "SCAN_QUEUE_STOP_EVENT", threading.Event())
    monkeypatch.setattr(root, "SCAN_QUEUE_WAKE_EVENT", threading.Event())
    monkeypatch.setattr(root, "SCAN_QUEUE_WORKER_THREAD", None)
    monkeypatch.setattr(root, "SCAN_QUEUE_PROCESSING_ACTIVE", False)

    api_lifecycle.shutdown()

    assert root.app.state.jellyfin_sync_scheduler is (None if ended else scheduler)


def test_runs_never_overlap(accounts, boundary, clock):
    registry = SyncRegistry()
    registry.lock.acquire()
    try:
        with pytest.raises(SyncInProgress):
            support._sync(accounts, boundary, clock, registry)
    finally:
        registry.lock.release()
    assert support._sync(accounts, boundary, clock, registry).trigger is SyncTrigger.MANUAL
    assert registry.last_run is not None and not registry.running


def test_the_scheduler_runs_at_once_then_every_five_minutes(accounts, boundary, clock):
    support._import(accounts, boundary, clock, frozenset({IDS["Blake"]}))
    registry = SyncRegistry()
    scheduler = JellyfinSyncScheduler(
        accounts.factory, clock=clock, adapters=support._adapters(boundary, clock), sync=registry
    )

    assert scheduler.tick() is not None
    for offset, runs in (
        (timedelta(minutes=4, seconds=59), False),
        (timedelta(minutes=5), True),
        (timedelta(minutes=9), False),
        (timedelta(minutes=10), True),
    ):
        clock.now = NOW + offset
        assert (scheduler.tick() is not None) is runs
    registry.lock.acquire()
    clock.now = NOW + timedelta(minutes=15)
    assert scheduler.tick() is None
    registry.lock.release()
    assert scheduler.next_due == NOW + timedelta(minutes=20)


def test_a_cancel_during_a_read_fetches_no_avatar_and_writes_nothing(accounts, boundary, clock):
    support._import(accounts, boundary, clock, frozenset({IDS["Casey"], IDS["Avery"]}))
    cancelled = threading.Event()
    calls = []

    class Cancelling:
        def verify_user(self, _identity):
            calls.append("verify")
            cancelled.set()
            return replace(jellyfin_user("Casey", verified_at=LATER), avatar_tag="f" * 32)

        def avatar(self, *_args):
            calls.append("avatar")

        def close(self) -> None:
            pass

    clock.now = LATER
    with accounts.factory() as db:
        run = SyncRegistry().run(
            db,
            SyncTrigger.SCHEDULED,
            clock=clock,
            adapters=lambda _db: Cancelling(),
            cancelled=cancelled,
        )

    assert calls == ["verify"]
    assert run.results == ()
