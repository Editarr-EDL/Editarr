from threading import Event

import pytest

from scanner import api, repository
from scanner.db import (
    ScanRow,
)
from scanner.runtime.process_control import WorkerIdentity
from tests.queue_support import _enqueue_scan, _queue_factory


def test_launch_cancellation_race_finishes_cancelled_and_releases_queue(monkeypatch):
    factory = _queue_factory()
    setup_db = factory()
    scan, queue_item = _enqueue_scan(setup_db, "cancel-race")
    setup_db.close()
    process_events: list[str] = []

    class Process:
        pid = 2808

        def terminate(self):
            raise AssertionError("the redirector must not be terminated after discovery")

    real_record = repository.record_scan_worker_process

    def cancel_before_record(db, scan_id, **kwargs):
        canceller = factory()
        try:
            assert repository.cancel_scan_queue_item(canceller, queue_item.queue_id) == (
                scan.id,
                True,
            )
        finally:
            canceller.close()
        db.expire_all()
        return real_record(db, scan_id, **kwargs)

    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        api.PROCESS_CONTROL,
        "launch_worker",
        lambda *_args, **_kwargs: Process(),
    )
    monkeypatch.setattr(
        api.PROCESS_CONTROL,
        "discover_identity",
        lambda scan_id, token, *, process_group=None, **_kwargs: WorkerIdentity(
            scan_id, 6344, process_group, token
        ),
    )
    monkeypatch.setattr(api.PROCESS_CONTROL, "ensure_worker_job", lambda _identity: None)
    monkeypatch.setattr(
        api.PROCESS_CONTROL,
        "request_stop",
        lambda identity: process_events.append(f"request:{identity.pid}") or True,
    )
    monkeypatch.setattr(
        api.PROCESS_CONTROL,
        "wait_for_exit",
        lambda identity, timeout: process_events.append(f"wait:{identity.pid}") or False,
    )
    monkeypatch.setattr(
        api.PROCESS_CONTROL,
        "force_stop",
        lambda identity: process_events.append(f"force:{identity.pid}") or True,
    )
    monkeypatch.setattr(api, "record_scan_worker_process", cancel_before_record)
    api.SCAN_QUEUE_STOP_EVENT.clear()

    assert not api._launch_scan_worker(scan.id)
    assert process_events == ["request:6344", "wait:6344", "force:6344"]

    check_db = factory()
    assert check_db.get(ScanRow, scan.id).status == "cancelled"
    assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id) is None
    check_db.close()


def test_launch_persists_proven_worker_pid_instead_of_windows_redirector(monkeypatch):
    factory = _queue_factory()
    setup_db = factory()
    scan, _queue_item = _enqueue_scan(setup_db, "redirector")
    setup_db.close()

    class Process:
        pid = 2808

        def terminate(self):
            raise AssertionError("proven worker launch must not be terminated")

    environments = []

    def launch(*_args, environment):
        environments.append(environment)
        return Process()

    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api.PROCESS_CONTROL, "launch_worker", launch)
    monkeypatch.setenv("EDITARR_DATA_DIR", "/stale/environment/data")
    monkeypatch.setattr(
        api.PROCESS_CONTROL,
        "discover_identity",
        lambda scan_id, token, *, process_group=None, **_kwargs: WorkerIdentity(
            scan_id, 6344, process_group, token
        ),
    )
    verified: list[WorkerIdentity] = []
    monkeypatch.setattr(api.PROCESS_CONTROL, "ensure_worker_job", verified.append)
    api.SCAN_QUEUE_STOP_EVENT.clear()

    assert api._launch_scan_worker(scan.id)
    check_db = factory()
    row = check_db.get(ScanRow, scan.id)
    assert row.worker_pid == 6344
    assert row.worker_pid != Process.pid
    assert verified and verified[0].pid == 6344
    assert environments[0]["EDITARR_DATA_DIR"] == str(api.settings.data_dir)
    assert environments[0]["EDITARR_DATABASE_URL"] == str(api.settings.database_url)
    check_db.close()


def test_launch_failure_marks_scan_resumable_and_keeps_queue_item(monkeypatch):
    factory = _queue_factory()
    setup_db = factory()
    scan, queue_item = _enqueue_scan(setup_db, "launch-failure")
    setup_db.close()

    def fail_to_start(*_args, **_kwargs):
        raise OSError("failed to exec worker")

    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api.PROCESS_CONTROL, "launch_worker", fail_to_start)
    api.SCAN_QUEUE_STOP_EVENT.clear()

    assert not api._launch_scan_worker(scan.id)

    check_db = factory()
    row = check_db.get(ScanRow, scan.id)
    assert row.status == "orphaned"
    assert row.error_message == "failed to exec worker"
    assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id).queue_id == (
        queue_item.queue_id
    )
    check_db.close()


def test_launch_failure_preserves_specific_error_recorded_by_worker(monkeypatch):
    factory = _queue_factory()
    setup_db = factory()
    scan, queue_item = _enqueue_scan(setup_db, "early-worker-failure")
    setup_db.close()

    def fail_to_start(*_args, **_kwargs):
        worker_db = factory()
        try:
            repository.mark_scan_orphaned(
                worker_db, scan.id, "Worker stopped", "pipeline dependency unavailable"
            )
        finally:
            worker_db.close()
        raise RuntimeError("Worker did not establish a token-authenticated identity")

    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api.PROCESS_CONTROL, "launch_worker", fail_to_start)
    api.SCAN_QUEUE_STOP_EVENT.clear()

    assert not api._launch_scan_worker(scan.id)
    check_db = factory()
    row = check_db.get(ScanRow, scan.id)
    assert row.status == "orphaned"
    assert row.error_message == "pipeline dependency unavailable"
    assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id).queue_id == (
        queue_item.queue_id
    )
    check_db.close()


def test_scheduler_marks_a_dead_dedicated_worker_orphaned(monkeypatch):
    factory = _queue_factory()
    setup_db = factory()
    scan, _queue_item = _enqueue_scan(setup_db, "dead-worker")
    assert repository.reserve_scan_worker(setup_db, scan.id, token="worker-token")
    assert repository.record_scan_worker_process(
        setup_db, scan.id, token="worker-token", pid=42, pgid=42
    )
    setup_db.close()
    stop_event = Event()

    def worker_is_gone(_scan_id, _pid, _pgid, _token):
        stop_event.set()
        return False

    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", stop_event)
    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api, "_worker_identity", worker_is_gone)
    cleaned: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        api,
        "cleanup_worker_control_endpoint",
        lambda scan_id, token: cleaned.append((scan_id, token)),
    )

    api._scan_queue_worker()

    check_db = factory()
    row = check_db.get(ScanRow, scan.id)
    assert row.status == "orphaned"
    assert row.error_message == "Dedicated scan worker is no longer running"
    assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id) is not None
    check_db.close()
    assert cleaned == [(scan.id, "worker-token")]


def test_scheduler_releases_a_cancelled_dead_worker_and_starts_next(monkeypatch):
    factory = _queue_factory()
    setup_db = factory()
    first_scan, first_queue = _enqueue_scan(setup_db, "first")
    second_scan, _second_queue = _enqueue_scan(setup_db, "second")
    assert repository.reserve_scan_worker(setup_db, first_scan.id, token="worker-token")
    assert repository.record_scan_worker_process(
        setup_db, first_scan.id, token="worker-token", pid=42, pgid=42
    )
    assert repository.cancel_scan_queue_item(setup_db, first_queue.queue_id) == (
        first_scan.id,
        True,
    )
    setup_db.close()
    stop_event = Event()
    launched: list[str] = []

    def launch(scan_id):
        launched.append(scan_id)
        stop_event.set()
        return True

    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", stop_event)
    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api, "_worker_identity", lambda _scan_id, _pid, _pgid, _token: None)
    monkeypatch.setattr(api, "_launch_scan_worker", launch)

    api._scan_queue_worker()

    assert launched == [second_scan.id]
    check_db = factory()
    assert check_db.get(ScanRow, first_scan.id).status == "cancelled"
    assert repository.get_scan_queue_item_by_scan_id(check_db, first_scan.id) is None
    assert repository.get_scan_queue_item_by_scan_id(check_db, second_scan.id) is not None
    check_db.close()


@pytest.mark.parametrize("terminal_status", ["complete", "cancelled", "failed"])
def test_scheduler_preserves_terminal_state_during_liveness_check(monkeypatch, terminal_status):
    factory = _queue_factory()
    setup_db = factory()
    scan, queue_item = _enqueue_scan(setup_db, "completion-race")
    assert repository.reserve_scan_worker(setup_db, scan.id, token="worker-token")
    assert repository.record_scan_worker_process(
        setup_db, scan.id, token="worker-token", pid=42, pgid=42
    )
    setup_db.close()
    stop_event = Event()

    def finish_before_identity_returns(*_args):
        with factory() as worker_db:
            repository.update_scan_status(worker_db, scan.id, terminal_status)
            repository.remove_scan_queue_item(worker_db, queue_item.queue_id)
            repository.clear_scan_worker(worker_db, scan.id)
        stop_event.set()
        return None

    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", stop_event)
    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api, "_worker_identity", finish_before_identity_returns)
    monkeypatch.setattr(api, "cleanup_worker_control_endpoint", lambda *_args: None)

    api._scan_queue_worker()

    with factory() as check_db:
        assert check_db.get(ScanRow, scan.id).status == terminal_status
        assert scan.id in {item.id for item in repository.list_scan_history(check_db)}
        assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id) is None


@pytest.mark.parametrize("change", ["cancellation", "replacement"])
def test_scheduler_rechecks_attempt_and_cancellation_after_liveness(monkeypatch, change):
    factory = _queue_factory()
    setup_db = factory()
    scan, queue_item = _enqueue_scan(setup_db, "changed-attempt")
    assert repository.reserve_scan_worker(setup_db, scan.id, token="old-worker")
    assert repository.record_scan_worker_process(
        setup_db, scan.id, token="old-worker", pid=42, pgid=42
    )
    setup_db.close()
    stop_event = Event()

    def change_before_identity_returns(*_args):
        with factory() as worker_db:
            if change == "cancellation":
                repository.cancel_scan_queue_item(worker_db, queue_item.queue_id)
            else:
                repository.mark_scan_orphaned(worker_db, scan.id, "Interrupted")
                assert repository.resume_orphaned_scan(worker_db, queue_item.queue_id)
                assert repository.reserve_scan_worker(worker_db, scan.id, token="new-worker")
                assert repository.record_scan_worker_process(
                    worker_db, scan.id, token="new-worker", pid=43, pgid=43
                )
        stop_event.set()
        return None

    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", stop_event)
    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api, "_worker_identity", change_before_identity_returns)
    monkeypatch.setattr(api, "cleanup_worker_control_endpoint", lambda *_args: None)

    api._scan_queue_worker()

    with factory() as check_db:
        row = check_db.get(ScanRow, scan.id)
        assert row.status == ("cancelled" if change == "cancellation" else "scanning")
        if change == "replacement":
            assert row.worker_token == "new-worker"
            assert row.worker_pid == 43
            assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id) is not None
        else:
            assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id) is None


def test_scheduler_does_not_delete_reused_queue_entry(monkeypatch):
    factory = _queue_factory()
    setup_db = factory()
    first, first_queue = _enqueue_scan(setup_db, "cancelled-old-queue")
    assert repository.reserve_scan_worker(setup_db, first.id, token="old-worker")
    assert repository.record_scan_worker_process(
        setup_db, first.id, token="old-worker", pid=42, pgid=42
    )
    repository.cancel_scan_queue_item(setup_db, first_queue.queue_id)
    setup_db.close()
    stop_event = Event()
    submitted = []

    def finish_and_enqueue_before_identity_returns(*_args):
        with factory() as worker_db:
            repository.update_scan_status(worker_db, first.id, "cancelled")
            repository.remove_scan_queue_item(worker_db, first_queue.queue_id)
            repository.clear_scan_worker(worker_db, first.id)
            second, second_queue = _enqueue_scan(worker_db, "new-queue")
            assert second_queue.queue_id == first_queue.queue_id
            submitted.append(second.id)
        stop_event.set()
        return None

    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", stop_event)
    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api, "_worker_identity", finish_and_enqueue_before_identity_returns)
    monkeypatch.setattr(api, "cleanup_worker_control_endpoint", lambda *_args: None)

    api._scan_queue_worker()

    with factory() as check_db:
        assert check_db.get(ScanRow, first.id).status == "cancelled"
        assert check_db.get(ScanRow, submitted[0]).status == "queued"
        assert repository.get_scan_queue_item_by_scan_id(check_db, submitted[0]) is not None
