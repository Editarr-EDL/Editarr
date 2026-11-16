"""MU-16: the media evidence service keeps the linked server's facts for the 24-hour grace.

The adapter is a scripted stand-in for `JellyfinUserAdapter` (the established
MU-08 boundary), returning MU-11's sealed fixture values (`mu11-media-access-4`)
or `Unverified`. Every time is fixed.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from scanner.db import UserProviderIdentityRow
from scanner.integrations.jellyfin_catalog import parse_catalog
from scanner.integrations.jellyfin_library_folders import parse_library_folders
from scanner.integrations.jellyfin_user_snapshot import (
    ItemAccess,
    ItemAccessSnapshot,
    Unverified,
    UnverifiedReason,
)
from scanner.security.access import link_state
from scanner.security.access.link_state import VERIFICATION_GRACE
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDenial, MediaDenied
from scanner.security.jellyfin_accounts import reads, store
from scanner.security.jellyfin_accounts.evidence import CachedItems, EvidenceCache, MediaEvidence
from scanner.security.media_access.matching import jellyfin_target
from scanner.security.media_access.service import decide_media, load_subject
from tests.jellyfin_user_boundary import IDS, NOW, PRIMARY
from tests.jellyfin_user_boundary import identity as user_identity
from tests.media_access_support import (
    FAMILY_FILM,
    JELLYFIN_ITEMS,
    MATURE,
    VIRTUAL_FOLDERS,
    jellyfin_user,
    listing,
    record,
    resolve,
)
from tests.multi_user_access_support import OWNER, USER, live_accounts

OUTAGE = Unverified(UnverifiedReason.UNREACHABLE)
REPLACED = Unverified(UnverifiedReason.SERVER_MISMATCH)
TARGETS = [
    jellyfin_target(PRIMARY, JELLYFIN_ITEMS[item], resolve) for item in (FAMILY_FILM, MATURE)
]


class ScriptedAdapter:
    """Answers like the MU-08 adapter from scripted values; counts every read."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.down = None
        self.reads = []
        self.closed = 0

    def _answer(self, name, value):
        self.reads.append(name)
        return self.down or value

    def catalog(self, server):
        records = [record(item) for item in JELLYFIN_ITEMS.values()]
        return self._answer("catalog", parse_catalog([listing(records)], server, self.clock()))

    def library_folders(self, server):
        return self._answer("folders", parse_library_folders(VIRTUAL_FOLDERS, server, self.clock()))

    def item_access(self, identity, item_ids):
        decisions = {FAMILY_FILM: ItemAccess.ALLOWED, MATURE: ItemAccess.RESTRICTED}
        snapshot = ItemAccessSnapshot(
            identity, {item: decisions[item] for item in item_ids}, self.clock()
        )
        return self._answer("items", snapshot)

    def close(self):
        self.closed += 1


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
def linked(tmp_path, clock):
    """User 4 linked to Casey, whose snapshot the last sync stored at NOW."""

    accounts = live_accounts(tmp_path)
    with accounts.factory() as db:
        db.add(
            UserProviderIdentityRow(
                user_id=USER,
                server_id=PRIMARY,
                provider_user_id=IDS["Casey"],
                link_state="verified",
            )
        )
        db.commit()
        store.apply(db, USER, jellyfin_user("Casey"), reads.begin_read(db, lambda: NOW))
    yield accounts
    accounts.close()


def _evidence(accounts, cache, user_id, clock):
    with accounts.factory() as db:
        access = accounts.access(user_id)
        subject = load_subject(db, access.principal.account)
        return subject, cache.evidence(db, access.principal.account, resolve, clock())


def _decide(subject, evidence: MediaEvidence, clock):
    with evidence:
        return decide_media(
            subject,
            TARGETS,
            user=evidence.user,
            catalog=evidence.catalog,
            libraries=evidence.libraries,
            items=evidence.items,
            now=clock(),
        )


def test_owner_and_local_accounts_need_no_evidence(linked, clock):
    cache = EvidenceCache(adapters=lambda db: pytest.fail("no Jellyfin read"))
    for user_id in (OWNER, USER + 1):
        assert _evidence(linked, cache, user_id, clock)[1] == MediaEvidence()


def test_linked_decisions_use_the_stored_snapshot_and_fresh_reads(linked, clock):
    adapter = ScriptedAdapter(clock)
    cache = EvidenceCache(adapters=lambda db: adapter)

    subject, evidence = _evidence(linked, cache, USER, clock)

    assert evidence.user == jellyfin_user("Casey")
    assert evidence.catalog.server_id == evidence.libraries.folders.server_id == PRIMARY
    decisions = _decide(subject, evidence, clock)
    assert list(decisions.values()) == [
        MediaAllowed(MediaBasis.JELLYFIN_VERIFIED),
        MediaDenied(MediaDenial.NOT_IN_SCOPE),
    ]
    assert adapter.reads == ["catalog", "folders", "items"] and adapter.closed == 1


def test_an_outage_falls_back_to_the_last_complete_reads_for_24_hours(linked, clock):
    adapter = ScriptedAdapter(clock)
    cache = EvidenceCache(adapters=lambda db: adapter)
    _decide(*_evidence(linked, cache, USER, clock), clock)

    clock.now = NOW + timedelta(minutes=4)
    _evidence(linked, cache, USER, clock)[1].close()
    assert adapter.reads.count("catalog") == 1

    adapter.down = OUTAGE
    clock.now = NOW + VERIFICATION_GRACE
    subject, evidence = _evidence(linked, cache, USER, clock)
    assert evidence.catalog.read_at == NOW
    assert list(_decide(subject, evidence, clock).values())[0] == MediaAllowed(
        MediaBasis.JELLYFIN_VERIFIED
    )

    clock.now = NOW + VERIFICATION_GRACE + timedelta(microseconds=1)
    subject, evidence = _evidence(linked, cache, USER, clock)
    assert evidence.catalog == OUTAGE
    assert set(_decide(subject, evidence, clock).values()) == {
        MediaDenied(MediaDenial.VERIFICATION_EXPIRED)
    }


def test_a_replaced_or_disconnected_server_denies_even_with_cached_reads(linked, clock):
    adapter = ScriptedAdapter(clock)
    cache = EvidenceCache(adapters=lambda db: adapter)
    _decide(*_evidence(linked, cache, USER, clock), clock)
    clock.now = NOW + timedelta(hours=1)

    adapter.down = REPLACED
    subject, evidence = _evidence(linked, cache, USER, clock)
    assert evidence.catalog == evidence.libraries == REPLACED
    assert set(_decide(subject, evidence, clock).values()) == {
        MediaDenied(MediaDenial.SERVER_REPLACED)
    }

    cache.adapters = lambda db: None
    subject, evidence = _evidence(linked, cache, USER, clock)
    assert set(_decide(subject, evidence, clock).values()) == {
        MediaDenied(MediaDenial.SERVER_REPLACED)
    }


def test_item_decisions_fall_back_per_item_only_when_each_was_seen(linked, clock):
    adapter = ScriptedAdapter(clock)
    cache = EvidenceCache(adapters=lambda db: adapter)
    subject, evidence = _evidence(linked, cache, USER, clock)
    identity = subject.identity
    assert isinstance(evidence.items.item_access(identity, [FAMILY_FILM]), ItemAccessSnapshot)

    adapter.down = OUTAGE
    clock.now = NOW + timedelta(hours=2)
    cached = evidence.items.item_access(identity, [FAMILY_FILM])
    assert (dict(cached.decisions), cached.verified_at) == ({FAMILY_FILM: ItemAccess.ALLOWED}, NOW)
    assert evidence.items.item_access(identity, [FAMILY_FILM, MATURE]) == OUTAGE
    clock.now = NOW + VERIFICATION_GRACE + timedelta(seconds=1)
    evidence.items.now = clock.now
    assert evidence.items.item_access(identity, [FAMILY_FILM]) == OUTAGE
    adapter.down = REPLACED
    assert evidence.items.item_access(identity, [FAMILY_FILM]) == REPLACED


def _held(answer):
    """A read of `answer` that starts, then waits for `release` before delivering."""

    read, release = threading.Event(), threading.Event()

    def held(*args):
        read.set()
        assert release.wait(10)
        return answer(*args)

    return read, release, held


class ItemAnswers:
    """Answers every item with `access` while up, and `down` once set."""

    def __init__(self, clock, access) -> None:
        self.clock, self.access, self.down = clock, access, None

    def item_access(self, who, item_ids):
        return self.down or ItemAccessSnapshot(
            who, dict.fromkeys(item_ids, self.access), self.clock()
        )


def test_an_older_item_answer_never_replaces_a_newer_one_or_becomes_the_fallback(clock):
    cache = EvidenceCache()
    casey = user_identity("Casey")
    allowed = ItemAnswers(clock, ItemAccess.ALLOWED)
    restricted = ItemAnswers(clock, ItemAccess.RESTRICTED)
    read, release, held = _held(allowed.item_access)
    allowed.item_access = held
    older = CachedItems(allowed, cache._items, clock())
    worker = threading.Thread(target=older.item_access, args=(casey, [FAMILY_FILM]))
    worker.start()
    try:
        assert read.wait(10)
        clock.now = NOW + timedelta(minutes=1)
        newer = CachedItems(restricted, cache._items, clock())
        assert newer.item_access(casey, [FAMILY_FILM]).decisions == {
            FAMILY_FILM: ItemAccess.RESTRICTED
        }
    finally:
        release.set()
        worker.join(10)

    restricted.down = OUTAGE
    clock.now = NOW + timedelta(minutes=2)
    fallback = CachedItems(restricted, cache._items, clock()).item_access(casey, [FAMILY_FILM])
    assert (dict(fallback.decisions), fallback.verified_at) == (
        {FAMILY_FILM: ItemAccess.RESTRICTED},
        NOW + timedelta(minutes=1),
    )


def test_an_older_catalog_read_never_replaces_a_newer_copy(clock):
    cache = EvidenceCache()
    adapter = ScriptedAdapter(clock)
    read, release, held = _held(adapter.catalog)
    worker = threading.Thread(target=cache._read, args=("catalog", PRIMARY, held, NOW))
    worker.start()
    try:
        assert read.wait(10)
        clock.now = NOW + timedelta(minutes=1)
        newer = cache._read("catalog", PRIMARY, adapter.catalog, clock())
    finally:
        release.set()
        worker.join(10)

    later = NOW + timedelta(minutes=10)
    assert cache._read("catalog", PRIMARY, lambda _server: OUTAGE, later) is newer
