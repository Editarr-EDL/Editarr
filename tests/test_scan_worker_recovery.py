"""Recovery and cancellation for dedicated scan workers."""

from contextlib import nullcontext

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner import repository, worker
from scanner.db import Base, ScanRow
from scanner.persistence.repository.scan_pause import pause_scan_queue_item
from scanner.runtime.worker_checkpoint_lease import checkpoint_writer_lease
from tests.test_dedicated_worker import _scan_with_queue
from tests.test_dedicated_worker import isolated_worker_paths as isolated_worker_paths


def test_orphaned_worker_is_adopted_or_can_resume_from_checkpoints():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    scan, queue_item = _scan_with_queue(db)
    assert repository.reserve_scan_worker(db, scan.id, token="worker-token")
    assert repository.record_scan_worker_process(db, scan.id, token="worker-token", pid=42, pgid=42)
    repository.mark_scan_checkpoint(db, scan.id, "visual")

    repository.mark_orphaned_scan_workers(db, {42})
    row = db.get(ScanRow, scan.id)
    assert row is not None
    assert row.status == "scanning"
    assert row.progress_message == "Reattached to active scan"

    assert repository.heartbeat_scan_worker(db, scan.id, "worker-token")
    adopted = db.get(ScanRow, scan.id)
    assert adopted.status == "scanning"
    assert adopted.progress_message == "Reattached to active scan"

    repository.mark_orphaned_scan_workers(db, set())
    started_at = db.get(ScanRow, scan.id).worker_started_at
    assert repository.resume_orphaned_scan(db, queue_item.queue_id)
    row = db.get(ScanRow, scan.id)
    assert row.status == "queued"
    assert row.worker_started_at == started_at
    assert row.visual_checkpoint_complete
    assert row.progress_message == "Queued to resume from the latest checkpoint"
    db.close()


@pytest.mark.parametrize("pause", [False, True])
def test_startup_and_quit_settle_only_the_attempt_they_probed(pause):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        scan, queued = _scan_with_queue(db)
        token = "worker-token"
        assert repository.reserve_scan_worker(db, scan.id, token=token)
        if pause:
            assert pause_scan_queue_item(db, queued.queue_id) == "pausing"
        original_status = "pausing" if pause else "scanning"
        before_registration = (None, None, token)
        repository.mark_orphaned_scan_workers(
            db, set(), active_attempts={(scan.id, token)},
            observed_attempts={scan.id: before_registration},
        )
        assert db.get(ScanRow, scan.id).status == original_status

        assert repository.record_scan_worker_process(db, scan.id, token=token, pid=42, pgid=42)
        repository.mark_orphaned_scan_workers(
            db, set(), observed_attempts={scan.id: before_registration},
        )
        assert not repository.mark_scan_orphaned(
            db, scan.id, "Stopped", settle_pause=True, observed_attempt=before_registration
        )
        assert db.get(ScanRow, scan.id).status == original_status

        assert repository.mark_scan_orphaned(
            db, scan.id, "Stopped", settle_pause=True, observed_attempt=(42, 42, token)
        )
        row = db.get(ScanRow, scan.id)
        assert row.status == ("paused" if pause else "orphaned")
        assert row.worker_token is None
        assert not repository.record_scan_worker_process(db, scan.id, token=token, pid=43, pgid=43)
    engine.dispose()


@pytest.mark.parametrize("writer_alive", [False, True])
def test_worker_observes_a_cancellation_committed_by_another_session(
    monkeypatch, tmp_path, writer_alive,
):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    scan, _queue_item = _scan_with_queue(setup_db)
    token = "worker-token"
    assert repository.reserve_scan_worker(setup_db, scan.id, token=token)
    setup_db.close()

    def fake_run_scan(db, _path, **kwargs):
        canceller = factory()
        try:
            row = canceller.get(ScanRow, scan.id)
            assert row is not None
            row.status = "cancelling"
            canceller.commit()
        finally:
            canceller.close()
        assert kwargs["should_cancel"]()
        kwargs["status_updater"](db, scan.id, "complete")
        pytest.fail("Worker completed a scan with a pending cancellation")

    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    monkeypatch.setattr(worker, "run_scan", fake_run_scan)

    lease = checkpoint_writer_lease(tmp_path, scan.id, token) if writer_alive else nullcontext()
    with lease:
        assert worker.run_scan_worker(scan.id, token) == 0

    check_db = factory()
    row = check_db.get(ScanRow, scan.id)
    assert row is not None
    assert row.status == ("cancelling" if writer_alive else "cancelled")
    if writer_alive:
        assert row.worker_token == token
        assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id) is not None
    check_db.close()


def test_worker_failure_is_resumable_and_keeps_its_queue_item(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    scan, queue_item = _scan_with_queue(setup_db)
    assert repository.reserve_scan_worker(setup_db, scan.id, token="worker-token")
    assert repository.record_scan_worker_process(
        setup_db, scan.id, token="worker-token", pid=42, pgid=42
    )
    setup_db.close()

    def fail_scan(*_args, **_kwargs):
        raise RuntimeError("decoder crashed")

    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    monkeypatch.setattr(worker, "run_scan", fail_scan)

    assert worker.run_scan_worker(scan.id, "worker-token") == 0

    check_db = factory()
    row = check_db.get(ScanRow, scan.id)
    assert row.status == "orphaned"
    assert row.error_message == "decoder crashed"
    assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id).queue_id == (
        queue_item.queue_id
    )
    assert repository.resume_orphaned_scan(check_db, queue_item.queue_id)
    assert check_db.get(ScanRow, scan.id).status == "queued"
    check_db.close()
