"""Failed launches preserve the worker-owned pause reservation until it can drain."""

import pytest

from scanner import api, repository
from scanner.persistence.repository.scan_pause import pause_scan_queue_item
from scanner.runtime.process_control import WorkerIdentity
from tests.queue_support import _enqueue_scan, _queue_factory


@pytest.mark.parametrize("failure", ["discovery", "registration"])
def test_pausing_failed_launch_remains_reserved_while_worker_stops(monkeypatch, failure):
    factory = _queue_factory()
    with factory() as db:
        scan, queue_item = _enqueue_scan(db, "pause-launch")
    signals = []

    class Process:
        pid = 2808

        def poll(self):
            return None

        def terminate(self):
            signals.append(self.pid)

    def discover(scan_id, token, **_kwargs):
        with factory() as db:
            assert pause_scan_queue_item(db, queue_item.queue_id) == "pausing"
        return WorkerIdentity(scan_id, 6344, None, token) if failure == "registration" else None

    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api, "_worker_identity", lambda *_args: None)
    monkeypatch.setattr(api, "record_scan_worker_process", lambda *_a, **_k: False)
    monkeypatch.setattr(api.PROCESS_CONTROL, "launch_worker", lambda *_a, **_k: Process())
    monkeypatch.setattr(api.PROCESS_CONTROL, "discover_identity", discover)
    monkeypatch.setattr(api.PROCESS_CONTROL, "ensure_worker_job", lambda _identity: None)
    monkeypatch.setattr(api.PROCESS_CONTROL, "request_stop", lambda _identity: False)
    monkeypatch.setattr(
        api.PROCESS_CONTROL, "force_stop", lambda identity: signals.append(identity.pid) or True
    )
    monkeypatch.setattr(api.PROCESS_CONTROL, "attempt_may_be_running", lambda *_a, **_k: True)
    api.SCAN_QUEUE_STOP_EVENT.clear()

    assert not api._launch_scan_worker(scan.id)

    expected_pid = 6344 if failure == "registration" else Process.pid
    assert signals == [expected_pid]
    with factory() as db:
        pid, _pgid, token, status = repository.scan_worker_process(db, queue_item.queue_id)
        assert pid == expected_pid and token is not None and status == "pausing"
        assert not repository.resume_orphaned_scan(db, queue_item.queue_id)


def test_failed_launch_settlement_does_not_fence_a_late_same_token_worker(monkeypatch):
    factory = _queue_factory()
    with factory() as db:
        scan, queue_item = _enqueue_scan(db, "late-pause-registration")

    class ExitedLauncher:
        pid = 2808

        def poll(self):
            return 1

    def discover(*_args, **_kwargs):
        with factory() as db:
            assert pause_scan_queue_item(db, queue_item.queue_id) == "pausing"
        return None

    def worker_may_be_alive(scan_id, pid, _pgid, token, **_kwargs):
        assert pid == ExitedLauncher.pid
        with factory() as db:
            assert repository.record_scan_worker_process(
                db, scan_id, token=token, pid=6344, pgid=None
            )
        return False  # The observed launcher had stopped, but a new identity is now registered.

    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api, "_worker_identity", lambda *_args: None)
    monkeypatch.setattr(api.PROCESS_CONTROL, "launch_worker", lambda *_a, **_k: ExitedLauncher())
    monkeypatch.setattr(api.PROCESS_CONTROL, "discover_identity", discover)
    monkeypatch.setattr(api.PROCESS_CONTROL, "attempt_may_be_running", worker_may_be_alive)
    monkeypatch.setattr(
        api, "cleanup_worker_control_endpoint", lambda *_: pytest.fail("live worker")
    )
    api.SCAN_QUEUE_STOP_EVENT.clear()

    assert not api._launch_scan_worker(scan.id)

    with factory() as db:
        pid, _pgid, token, status = repository.scan_worker_process(db, queue_item.queue_id)
        assert pid == 6344 and token is not None and status == "pausing"
        assert not repository.resume_orphaned_scan(db, queue_item.queue_id)
