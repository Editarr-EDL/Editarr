"""Worker evidence collection never holds SQLite's writer reservation."""

import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from threading import Thread

import pytest
from sqlalchemy import text

from scanner.db import MediaItemRow, ScanRow
from scanner.integrations.jellyfin_user_snapshot import ItemAccess
from scanner.persistence.rows.db_auth import AccountSettingsRow
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.identity import AccountStatus
from scanner.security.access.overrides import OverrideEffect
from scanner.security.jellyfin_accounts import reads, store
from scanner.security.jellyfin_accounts.evidence import evidence_cache
from scanner.security.scan_jobs import authority, media
from tests.media_access_support import FAMILY, FAMILY_FILM, jellyfin_user, resolve
from tests.multi_user_access_support import USER
from tests.scan_access_http import ScanApp
from tests.test_jellyfin_account_evidence import ScriptedAdapter
from tests.test_jellyfin_account_evidence import clock as clock
from tests.test_jellyfin_account_evidence import linked as linked


def linked_gate(linked, clock, monkeypatch, tmp_path, adapter):
    app = ScanApp(None, linked, tmp_path)
    scan_id, _ = app.seed_scan(USER, "linked-boundary")
    with linked.factory() as db:
        scan = db.get(ScanRow, scan_id)
        scan.resolved_local_path = resolve("/media/family/film.mkv")
        item = db.get(MediaItemRow, scan.media_item_id)
        item.jellyfin_id, item.library_id = FAMILY_FILM, FAMILY
        db.commit()
    monkeypatch.setattr(media, "_resolver", lambda _db: resolve)
    monkeypatch.setattr(evidence_cache, "adapters", lambda _db: adapter(clock))
    monkeypatch.setattr(evidence_cache, "_copies", {})
    monkeypatch.setattr(evidence_cache, "_items", {})
    return authority.JobGate(linked.factory, scan_id, clock=clock)


def test_unrelated_writer_commits_during_slow_linked_worker_check(
    linked, clock, monkeypatch, tmp_path
):
    outcome = []

    def unrelated_write():
        try:
            with linked.factory() as db:
                db.execute(text("UPDATE users SET id = id WHERE id = 1"))
                db.commit()
            outcome.append("committed")
        except Exception as error:
            outcome.append(str(error))

    class SlowAdapter(ScriptedAdapter):
        def item_access(self, identity, item_ids):
            thread = Thread(target=unrelated_write)
            thread.start()
            time.sleep(5.5)
            thread.join(timeout=10)
            answer = super().item_access(identity, item_ids)
            return replace(answer, decisions={item: ItemAccess.ALLOWED for item in item_ids})

    gate = linked_gate(linked, clock, monkeypatch, tmp_path, SlowAdapter)
    with linked.factory() as db:
        gate.run(db, lambda _db: outcome.append("worker write"))
    assert outcome == ["committed", "worker write"]


def test_linked_polls_coalesce_decisions_without_writer_locks(linked, clock, monkeypatch, tmp_path):
    calls = {"item": 0, "catalog": 0, "lock": 0, "decision": 0}

    class CountingAdapter(ScriptedAdapter):
        def catalog(self, server):
            calls["catalog"] += 1
            return super().catalog(server)

        def item_access(self, identity, item_ids):
            calls["item"] += 1
            answer = super().item_access(identity, item_ids)
            return replace(answer, decisions={item: ItemAccess.ALLOWED for item in item_ids})

    real_lock, real_decide = authority.write_lock, authority.decide_job

    @contextmanager
    def counted_lock(db):
        calls["lock"] += 1
        with real_lock(db):
            yield

    def counted_decide(*args, **kwargs):
        calls["decision"] += 1
        return real_decide(*args, **kwargs)

    gate = linked_gate(linked, clock, monkeypatch, tmp_path, CountingAdapter)
    monkeypatch.setattr(authority, "monotonic", lambda: 1.0)
    monkeypatch.setattr(authority, "write_lock", counted_lock)
    monkeypatch.setattr(authority, "decide_job", counted_decide)
    for _poll in range(50):
        gate.require()
    assert calls == {"item": 1, "catalog": 1, "lock": 0, "decision": 1}
    with linked.factory() as db:
        gate.run(db, lambda _db: None)
    assert calls["lock"] == 1
    assert calls["decision"] == 2
    assert calls["item"] == 1


@pytest.mark.parametrize("change", ["generation", "epoch"])
def test_worker_retries_evidence_after_ordering_changes(
    linked, clock, monkeypatch, tmp_path, change
):
    calls, writes = [], []

    class RacingAdapter(ScriptedAdapter):
        def item_access(self, identity, item_ids):
            calls.append("read")
            if len(calls) == 1:
                with linked.factory() as db:
                    if change == "generation":
                        reads.begin_read(db, clock)
                    else:
                        db.get(AccountSettingsRow, 1).jellyfin_read_epoch = "f" * 32
                        db.commit()
            answer = super().item_access(identity, item_ids)
            return replace(answer, decisions={item: ItemAccess.ALLOWED for item in item_ids})

    gate = linked_gate(linked, clock, monkeypatch, tmp_path, RacingAdapter)
    with linked.factory() as db:
        gate.run(db, lambda _db: writes.append(len(calls)))
    assert calls == ["read", "read"]
    assert writes == [2]


def test_worker_poll_retries_evidence_after_unrelated_ordering_change(
    linked, clock, monkeypatch, tmp_path
):
    calls = []

    class RacingAdapter(ScriptedAdapter):
        def item_access(self, identity, item_ids):
            calls.append("read")
            if len(calls) == 1:
                with linked.factory() as db:
                    reads.begin_read(db, clock)
            answer = super().item_access(identity, item_ids)
            return replace(answer, decisions={item: ItemAccess.ALLOWED for item in item_ids})

    gate = linked_gate(linked, clock, monkeypatch, tmp_path, RacingAdapter)
    assert gate.require() is not None
    assert calls == ["read", "read"]


def test_worker_poll_evidence_retry_is_bounded(linked, clock, monkeypatch, tmp_path):
    calls = []

    class UnstableAdapter(ScriptedAdapter):
        def item_access(self, identity, item_ids):
            calls.append("read")
            with linked.factory() as db:
                reads.begin_read(db, clock)
            answer = super().item_access(identity, item_ids)
            return replace(answer, decisions={item: ItemAccess.ALLOWED for item in item_ids})

    gate = linked_gate(linked, clock, monkeypatch, tmp_path, UnstableAdapter)
    with pytest.raises(authority.JobRefusedError):
        gate.require()
    assert len(calls) == authority.MAX_EVIDENCE_ATTEMPTS


@pytest.mark.parametrize("change", ["disabled", "capability", "policy", "clock_fence"])
def test_evidence_collected_before_revocation_cannot_authorize_a_write(
    linked, clock, monkeypatch, tmp_path, change
):
    writes, calls = [], []

    class RevokingAdapter(ScriptedAdapter):
        def item_access(self, identity, item_ids):
            calls.append("read")
            if len(calls) == 1:
                if change == "disabled":
                    linked.set_status(USER, AccountStatus.DISABLED)
                elif change == "capability":
                    linked.set_override(USER, C.SCANS_START, OverrideEffect.DENY)
                else:
                    with linked.factory() as db:
                        if change == "clock_fence":
                            clock.now -= timedelta(seconds=1)
                            reads.begin_read(db, clock)
                        else:
                            user = jellyfin_user("Casey")
                            snapshot = replace(
                                user,
                                media=replace(
                                    user.media, all_libraries=False, library_ids=frozenset()
                                ),
                            )
                            store.apply(db, USER, snapshot, reads.begin_read(db, clock))
            answer = super().item_access(identity, item_ids)
            access = (
                ItemAccess.RESTRICTED
                if change == "policy" and len(calls) > 1
                else ItemAccess.ALLOWED
            )
            return replace(answer, decisions={item: access for item in item_ids})

    gate = linked_gate(linked, clock, monkeypatch, tmp_path, RevokingAdapter)
    with linked.factory() as db, pytest.raises(authority.JobRefusedError):
        gate.run(db, lambda _db: writes.append("unauthorized"))
    assert writes == []


def test_evidence_retry_is_bounded(linked, clock, monkeypatch, tmp_path):
    calls, writes = [], []

    class UnstableAdapter(ScriptedAdapter):
        def item_access(self, identity, item_ids):
            calls.append("read")
            with linked.factory() as db:
                reads.begin_read(db, clock)
            return super().item_access(identity, item_ids)

    gate = linked_gate(linked, clock, monkeypatch, tmp_path, UnstableAdapter)
    with linked.factory() as db, pytest.raises(authority.JobRefusedError):
        gate.run(db, lambda _db: writes.append("unauthorized"))
    assert len(calls) == authority.MAX_EVIDENCE_ATTEMPTS
    assert writes == []


def test_linked_poll_refreshes_at_the_interval_and_immediately_on_a_revision(
    linked, clock, monkeypatch, tmp_path
):
    calls, tick = [], [1.0]

    class CountingAdapter(ScriptedAdapter):
        def item_access(self, identity, item_ids):
            calls.append("read")
            return super().item_access(identity, item_ids)

    gate = linked_gate(linked, clock, monkeypatch, tmp_path, CountingAdapter)
    monkeypatch.setattr(authority, "monotonic", lambda: tick[0])
    gate.require()
    gate.require()
    assert calls == ["read"]
    tick[0] += authority.RECHECK_SECONDS
    gate.require()
    assert calls == ["read", "read"]
    linked.set_override(USER, C.SCANS_START, OverrideEffect.DENY)
    with pytest.raises(authority.JobRefusedError):
        gate.require()
