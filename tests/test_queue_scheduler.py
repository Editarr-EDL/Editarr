from threading import Event, Thread
from types import SimpleNamespace

from scanner import api, repository
from scanner.db import (
    ScanRow,
)
from tests.queue_support import DummyDb, _enqueue_scan, _queue_factory


def test_scan_queue_worker_starts_a_dedicated_worker_for_the_next_item(monkeypatch):
    stop_event = Event()
    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", stop_event)
    events: list[tuple[str, object]] = []
    item = SimpleNamespace(id="scan-1")
    monkeypatch.setattr(api, "get_session_factory", lambda: DummyDb)
    monkeypatch.setattr(api, "get_active_scan_queue_item", lambda _db: None)
    monkeypatch.setattr(api, "get_next_scan_queue", lambda _db: item)

    def fake_launch(scan_id):
        events.append(("launch", scan_id))
        stop_event.set()
        return True

    monkeypatch.setattr(api, "_launch_scan_worker", fake_launch)
    api._scan_queue_worker()

    assert events == [("launch", "scan-1")]


def test_scan_queue_worker_waits_for_work_instead_of_exiting(monkeypatch):
    stop_event = Event()
    wake_event = Event()
    wake_event.set()
    item = SimpleNamespace(id="scan-1")
    next_items = iter([None, item])
    launches: list[str] = []

    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", stop_event)
    monkeypatch.setattr(api, "SCAN_QUEUE_WAKE_EVENT", wake_event)
    monkeypatch.setattr(api, "get_session_factory", lambda: DummyDb)
    monkeypatch.setattr(api, "get_active_scan_queue_item", lambda _db: None)
    monkeypatch.setattr(api, "get_next_scan_queue", lambda _db: next(next_items))

    def fake_launch(scan_id):
        launches.append(scan_id)
        stop_event.set()
        return True

    monkeypatch.setattr(api, "_launch_scan_worker", fake_launch)

    api._scan_queue_worker()

    assert launches == ["scan-1"]


def test_starting_an_active_scheduler_wakes_it_for_new_work(monkeypatch):
    stop_event = Event()
    wake_event = Event()
    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", stop_event)
    monkeypatch.setattr(api, "SCAN_QUEUE_WAKE_EVENT", wake_event)
    monkeypatch.setattr(api, "SCAN_QUEUE_WORKER_RUNNING", True)
    monkeypatch.setattr(
        api,
        "SCAN_QUEUE_WORKER_THREAD",
        SimpleNamespace(is_alive=lambda: True),
    )

    api._start_scan_queue_worker_if_needed()

    assert wake_event.is_set()


def test_starting_scheduler_replaces_stale_thread_and_launches_real_queued_row(
    monkeypatch,
):
    factory = _queue_factory()
    setup_db = factory()
    scan, _queue_item = _enqueue_scan(setup_db, "stale-scheduler")
    setup_db.close()
    stop_event = Event()
    wake_event = Event()
    launched = Event()
    launched_scan_ids: list[str] = []
    stale_thread = Thread(target=lambda: None)
    stale_thread.start()
    stale_thread.join()

    def launch(scan_id):
        launched_scan_ids.append(scan_id)
        launched.set()
        stop_event.set()
        return True

    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", stop_event)
    monkeypatch.setattr(api, "SCAN_QUEUE_WAKE_EVENT", wake_event)
    monkeypatch.setattr(api, "SCAN_QUEUE_WORKER_RUNNING", True)
    monkeypatch.setattr(api, "SCAN_QUEUE_WORKER_THREAD", stale_thread)
    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api, "_launch_scan_worker", launch)

    api._start_scan_queue_worker_if_needed()

    assert launched.wait(timeout=2)
    assert launched_scan_ids == [scan.id]


def test_only_one_scheduler_session_can_reserve_a_queued_scan():
    factory = _queue_factory()
    setup_db = factory()
    scan, _queue_item = _enqueue_scan(setup_db, "reservation-race")
    setup_db.close()
    first_scheduler = factory()
    second_scheduler = factory()
    # Load the same queued row into both identity maps before either scheduler
    # tries to claim it, reproducing the stale-read window in the old code.
    assert first_scheduler.get(ScanRow, scan.id).status == "queued"
    assert second_scheduler.get(ScanRow, scan.id).status == "queued"

    assert repository.reserve_scan_worker(
        first_scheduler, scan.id, token="first-worker"
    )
    assert not repository.reserve_scan_worker(
        second_scheduler, scan.id, token="second-worker"
    )

    first_scheduler.expire_all()
    row = first_scheduler.get(ScanRow, scan.id)
    assert row.status == "scanning"
    assert row.worker_token == "first-worker"
    first_scheduler.close()
    second_scheduler.close()


def test_idle_scheduler_periodically_rechecks_persisted_queue(monkeypatch):
    stop_event = Event()
    item = SimpleNamespace(id="scan-after-timeout")
    next_items = iter([None, item])
    wait_timeouts: list[float | None] = []

    class WakeEvent:
        def wait(self, timeout=None):
            wait_timeouts.append(timeout)
            return False

        def clear(self):
            return None

    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", stop_event)
    monkeypatch.setattr(api, "SCAN_QUEUE_WAKE_EVENT", WakeEvent())
    monkeypatch.setattr(api, "get_session_factory", lambda: DummyDb)
    monkeypatch.setattr(api, "get_active_scan_queue_item", lambda _db: None)
    monkeypatch.setattr(api, "get_next_scan_queue", lambda _db: next(next_items))

    def launch(scan_id):
        assert scan_id == item.id
        stop_event.set()
        return True

    monkeypatch.setattr(api, "_launch_scan_worker", launch)

    api._scan_queue_worker()

    assert wait_timeouts == [1]


def test_shutdown_stops_the_scheduler_without_stopping_the_dedicated_worker(monkeypatch):
    joins: list[float] = []

    class Worker:
        def is_alive(self):
            return True

        def join(self, timeout):
            joins.append(timeout)

    api.SCAN_QUEUE_STOP_EVENT.clear()
    monkeypatch.setattr(api, "SCAN_QUEUE_WORKER_THREAD", Worker())

    api.shutdown()

    assert api.SCAN_QUEUE_STOP_EVENT.is_set()
    assert joins == [5]

    api.SCAN_QUEUE_STOP_EVENT.clear()


