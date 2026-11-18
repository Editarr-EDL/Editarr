"""Scan duration is the sum of active worker attempts."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scanner import repository
from scanner.persistence.repository import repository_queue, scan_timing
from tests.queue_support import _enqueue_scan, _queue_factory


@pytest.fixture
def clock(monkeypatch):
    start = datetime(2026, 11, 17, tzinfo=UTC)
    clock = SimpleNamespace(time=start, now=lambda _tz: clock.time)
    monkeypatch.setattr(repository_queue, "datetime", clock)
    monkeypatch.setattr(scan_timing, "datetime", clock)

    def at(seconds):
        clock.time = start + timedelta(seconds=seconds)
        return clock.time

    return at


def test_failed_attempt_pauses_through_retry_and_history_edits(clock):
    factory = _queue_factory()
    with factory() as db:
        scan, queued = _enqueue_scan(db, "timed")
        assert queued.scan_duration_seconds is None
        assert repository.reserve_scan_worker(db, scan.id, token="attempt-1")

    with factory() as db:
        clock(10)
        assert repository.heartbeat_scan_worker(db, scan.id, "attempt-1")
        sampled_at = clock(14)
        active = repository.get_scan_queue_item_by_scan_id(db, scan.id)
        assert active.scan_duration_seconds == 4
        assert active.scan_duration_as_of == sampled_at

        clock(20)
        repository.update_scan_status(db, scan.id, "error")
        clock(21)
        repository.mark_scan_orphaned(db, scan.id, "Interrupted", "Retryable failure")
        assert not repository.heartbeat_scan_worker(db, scan.id, "attempt-1")

    with factory() as db:
        clock(3_600)
        paused = repository.get_scan_queue_item_by_scan_id(db, scan.id)
        assert paused.status == "orphaned"
        assert paused.scan_duration_seconds == 10
        assert paused.scan_duration_as_of is None

        assert repository.resume_orphaned_scan(db, queued.queue_id)
        clock(3_650)
        waiting = repository.get_scan_queue_item_by_scan_id(db, scan.id)
        assert waiting.status == "queued"
        assert waiting.scan_duration_seconds == 10
        assert waiting.scan_duration_as_of is None

        assert repository.reserve_scan_worker(db, scan.id, token="attempt-2")
        launching = repository.get_scan_queue_item_by_scan_id(db, scan.id)
        assert launching.scan_duration_seconds == 10
        assert launching.scan_duration_as_of is None

        clock(3_655)
        assert repository.heartbeat_scan_worker(db, scan.id, "attempt-2")
        sampled_at = clock(3_662)
        active = repository.get_scan_queue_item_by_scan_id(db, scan.id)
        assert active.scan_started_at == paused.scan_started_at
        assert active.scan_duration_seconds == 17
        assert active.scan_duration_as_of == sampled_at

        clock(3_665)
        repository.update_scan_status(db, scan.id, "complete")
        repository.remove_scan_queue_item(db, queued.queue_id)
        clock(4_000)
        repository.clear_scan_worker(db, scan.id)
        renamed = repository.rename_scan(db, scan.id, "Retained scan")
        assert renamed.scan_duration_seconds == 20
        assert renamed.scan_duration_as_of is None
        assert repository.list_scan_history(db)[0].scan_duration_seconds == 20


@pytest.mark.parametrize("cancelling", [False, True])
def test_missing_worker_stops_at_its_last_heartbeat(clock, cancelling):
    with _queue_factory()() as db:
        scan, queued = _enqueue_scan(db, "stopped")
        assert repository.reserve_scan_worker(db, scan.id, token="attempt")
        assert repository.heartbeat_scan_worker(db, scan.id, "attempt")
        clock(15)
        assert repository.heartbeat_scan_worker(db, scan.id, "attempt")
        if cancelling:
            clock(16)
            assert repository.cancel_scan_queue_item(db, queued.queue_id) == (scan.id, True)
        clock(3_600)
        repository_queue.settle_stopped_scan_worker(db, scan.id, token="attempt")

        if cancelling:
            settled = repository.list_scan_history(db)[0]
            assert settled.status == "cancelled"
        else:
            settled = repository.get_scan_queue_item_by_scan_id(db, scan.id)
            assert settled.status == "orphaned"
        assert settled.scan_duration_seconds == 15
        assert settled.scan_duration_as_of is None
