import builtins
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner import repository, worker
from scanner.credits.credits_detector import default_configuration
from scanner.db import Base, ScanRow
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.models import ScanMode
from scanner.pipeline.audio_tracks import AudioTrack, AudioTrackSelection
from scanner.runtime.maintenance import MaintenanceUnavailable
from scanner.runtime.process_control import ProcessControl
from tests.scan_access_support import owner_caller


@pytest.fixture(autouse=True)
def isolated_worker_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(worker, "require_supported_schema", lambda: "0001_current_baseline")
    monkeypatch.setattr(worker, "require_owner_setup", lambda: None)


def test_worker_refuses_to_run_before_owner_setup(monkeypatch, tmp_path):
    from scanner.persistence.migrations import preflight
    from scanner.persistence.migrations.runner import migrate_fresh_database

    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    monkeypatch.setattr(preflight, "database_location", lambda: (path, tmp_path))
    monkeypatch.setattr(worker, "require_owner_setup", preflight.require_owner_setup)
    monkeypatch.setattr(
        worker, "_run_scan_worker", lambda *_args: pytest.fail("worker ran without an owner")
    )
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="Owner setup is required"):
        worker.run_scan_worker("scan-1", "worker-token")
    assert path.read_bytes() == before

    factory = sessionmaker(bind=create_engine(f"sqlite:///{path}"))
    with factory() as db:
        from tests.multi_user_access_support import add_linked_owner

        add_linked_owner(db, "Owner")
    monkeypatch.setattr(worker, "_run_scan_worker", lambda *_args: 0)
    assert worker.run_scan_worker("scan-1", "worker-token") == 0


def _scan_with_queue(db):
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path="/media/example.mkv",
    )
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path="/media/example.mkv",
        duration_seconds=60,
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=True,
        video_model="nudenet",
        frame_interval=2,
        speed_up=False,
        initiator=owner_caller(db).initiator,
    )
    selection = AudioSelection.from_selection(
        AudioTrackSelection(
            "a" * 64, AudioTrack(1, "eng", "Main", "aac", 2, 1, 0.0, 60.0, "1/48000")
        )
    )
    db.get(ScanRow, scan.id).requested_audio_selection_json = selection.model_dump_json()
    db.commit()
    return scan, repository.enqueue_scan(db, scan.id)


def _credits_scan_with_queue(db):
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path="/media/example.mkv",
    )
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path="/media/example.mkv",
        duration_seconds=60,
        scan_audio=False,
        audio_model=None,
        audio_filter=None,
        scan_video=True,
        video_model=None,
        scan_mode=ScanMode.CREDITS_DETECTION,
        credits_detection_configuration=default_configuration(qwen_model=None),
        initiator=owner_caller(db).initiator,
    )
    return scan, repository.enqueue_scan(db, scan.id)


def test_worker_prevents_idle_system_sleep_on_macos(monkeypatch):
    calls = []

    class CaffeinateProcess:
        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout=None):
            calls.append(("wait", timeout))

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return CaffeinateProcess()

    monkeypatch.setattr(worker.sys, "platform", "darwin")
    monkeypatch.setattr(worker.os, "getpid", lambda: 123)
    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)

    with worker._prevent_idle_system_sleep():
        pass

    assert calls == [
        (
            ["caffeinate", "-i", "-w", "123"],
            {"stdout": worker.subprocess.DEVNULL, "stderr": worker.subprocess.DEVNULL},
        ),
        "terminate",
        ("wait", 2),
    ]


def test_worker_persists_checkpoints_and_releases_the_queue(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    scan, _queue_item = _scan_with_queue(setup_db)
    assert repository.reserve_scan_worker(setup_db, scan.id, token="worker-token")
    assert repository.record_scan_worker_process(
        setup_db, scan.id, token="worker-token", pid=42, pgid=42
    )
    setup_db.close()

    def fake_run_scan(db, _path, **kwargs):
        assert kwargs["completed_stages"] == set()
        for stage, effective in (
            ("visual", {"video_model": "nudenet", "coarse_scan_enabled": False}),
            ("audio_extract", {"source": "video"}),
            ("audio_scan", {"audio_model": "whisper", "audio_filter": "better_profanity"}),
        ):
            kwargs["stage_callback"](stage, "started", effective)
            kwargs["stage_results_writer"](stage, [])
            kwargs["stage_callback"](stage, "complete", effective)
        kwargs["status_updater"](db, kwargs["video_id"], "complete")

    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    monkeypatch.setattr(worker, "run_scan", fake_run_scan)

    assert worker.run_scan_worker(scan.id, "worker-token") == 0

    check_db = factory()
    row = check_db.get(ScanRow, scan.id)
    assert row is not None
    assert row.status == "complete"
    assert row.visual_checkpoint_complete
    assert row.audio_extraction_checkpoint_complete
    assert row.audio_checkpoint_complete
    assert row.worker_pid is None
    assert row.worker_started_at is not None
    history_item = repository.list_scan_history(check_db)[0]
    assert history_item.scan_started_at == row.worker_started_at
    assert history_item.scan_duration_seconds is not None
    assert history_item.scan_duration_seconds >= 0
    assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id) is None
    check_db.close()


def test_worker_pauses_without_recovery_writes_when_maintenance_blocks_a_stage(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as setup_db:
        scan, _queue_item = _scan_with_queue(setup_db)
        assert repository.reserve_scan_worker(setup_db, scan.id, token="worker-token")

    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)

    def blocked_stage(*_args, **_kwargs):
        raise MaintenanceUnavailable("Database maintenance is pending")

    monkeypatch.setattr(worker, "run_scan", blocked_stage)
    assert worker.run_scan_worker(scan.id, "worker-token") == 0

    with factory() as check_db:
        assert check_db.get(ScanRow, scan.id).status == "scanning"
        assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id) is not None


def test_worker_dispatches_credits_scan_and_releases_the_queue(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    scan, _queue_item = _credits_scan_with_queue(setup_db)
    assert repository.reserve_scan_worker(setup_db, scan.id, token="worker-token")
    assert repository.record_scan_worker_process(
        setup_db, scan.id, token="worker-token", pid=42, pgid=42
    )
    setup_db.close()
    calls = []

    def fake_run_credits_scan(db, _path, **kwargs):
        calls.append(kwargs)
        kwargs["status_updater"](db, kwargs["scan_id"], "complete")

    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    monkeypatch.setattr(worker, "run_scan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "run_credits_scan", fake_run_credits_scan)

    assert worker.run_scan_worker(scan.id, "worker-token") == 0

    assert len(calls) == 1
    assert calls[0]["configuration"].qwen_model is None
    assert callable(calls[0]["should_cancel"])
    check_db = factory()
    assert check_db.get(ScanRow, scan.id).status == "complete"
    assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id) is None
    check_db.close()


def test_credits_worker_failure_is_terminal_and_releases_the_queue(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    scan, _queue_item = _credits_scan_with_queue(setup_db)
    assert repository.reserve_scan_worker(setup_db, scan.id, token="worker-token")
    setup_db.close()

    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        worker,
        "run_credits_scan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("detector failed")),
    )

    assert worker.run_scan_worker(scan.id, "worker-token") == 0

    check_db = factory()
    row = check_db.get(ScanRow, scan.id)
    assert row.status == "failed"
    assert row.error_message == "detector failed"
    assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id) is None
    assert row.worker_pid is None
    assert [item.id for item in repository.list_scan_history(check_db)] == [scan.id]
    check_db.close()


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
    from scanner.persistence.repository.scan_pause import pause_scan_queue_item

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


def test_worker_observes_a_cancellation_committed_by_another_session(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    scan, _queue_item = _scan_with_queue(setup_db)
    assert repository.reserve_scan_worker(setup_db, scan.id, token="worker-token")
    setup_db.close()

    def fake_run_scan(_db, _path, **kwargs):
        canceller = factory()
        try:
            row = canceller.get(ScanRow, scan.id)
            assert row is not None
            row.status = "cancelling"
            canceller.commit()
        finally:
            canceller.close()
        assert kwargs["should_cancel"]()
        raise worker.ScanCancelledError("Scan cancelled")

    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    monkeypatch.setattr(worker, "run_scan", fake_run_scan)

    assert worker.run_scan_worker(scan.id, "worker-token") == 0

    check_db = factory()
    row = check_db.get(ScanRow, scan.id)
    assert row is not None
    assert row.status == "cancelled"
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


@pytest.mark.parametrize("problem", ["missing", "relabelled", "unknown_checkpoint"])
def test_worker_never_substitutes_unknown_or_relabelled_audio(monkeypatch, problem):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        scan, _queue = _scan_with_queue(db)
        row = db.get(ScanRow, scan.id)
        if problem == "missing":
            row.requested_audio_selection_json = None
        elif problem == "relabelled":
            row.analyzed_audio_selection_json = row.requested_audio_selection_json.replace(
                '"Main"', '"Commentary"'
            )
        db.commit()
        assert repository.reserve_scan_worker(db, scan.id, token="worker-token")
        assert repository.record_scan_worker_process(
            db, scan.id, token="worker-token", pid=42, pgid=42
        )
    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        worker, "run_scan", lambda *_args, **_kwargs: pytest.fail("unknown audio executed")
    )
    if problem == "unknown_checkpoint":
        monkeypatch.setattr(
            worker, "scan_checkpoint_stages", lambda *_args, **_kwargs: {"audio_scan"}
        )
    try:
        assert worker.run_scan_worker(scan.id, "worker-token") == 0
        with factory() as db:
            row = db.get(ScanRow, scan.id)
            assert row.status == "orphaned"
            assert "start a new scan" in row.error_message
    finally:
        engine.dispose()


def test_worker_handles_queue_lookup_failure_without_masking_the_error(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    scan, queue_item = _scan_with_queue(setup_db)
    assert repository.reserve_scan_worker(setup_db, scan.id, token="worker-token")
    setup_db.close()

    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        worker,
        "get_scan_queue_item_by_scan_id",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("queue lookup failed")),
    )

    assert worker.run_scan_worker(scan.id, "worker-token") == 0

    check_db = factory()
    row = check_db.get(ScanRow, scan.id)
    assert row.status == "orphaned"
    assert row.error_message == "queue lookup failed"
    assert check_db.get(repository.ScanQueueRow, queue_item.queue_id) is not None
    check_db.close()


def test_worker_proves_identity_before_pipeline_import_and_preserves_import_failure(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    scan, queue_item = _scan_with_queue(setup_db)
    assert repository.reserve_scan_worker(setup_db, scan.id, token="worker-token")
    setup_db.close()
    original_import = builtins.__import__
    proven = []

    def fail_pipeline_import(name, *args, **kwargs):
        if name == "scanner.pipeline.runner":
            identity = ProcessControl().identity(scan.id, os.getpid(), None, "worker-token")
            assert identity is not None
            proven.append(identity)
            raise ImportError("pipeline dependency unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    monkeypatch.setattr(builtins, "__import__", fail_pipeline_import)

    assert worker.run_scan_worker(scan.id, "worker-token") == 0
    assert len(proven) == 1
    assert ProcessControl().identity(scan.id, os.getpid(), None, "worker-token") is None
    check_db = factory()
    row = check_db.get(ScanRow, scan.id)
    assert row.status == "orphaned"
    assert row.error_message == "pipeline dependency unavailable"
    assert repository.get_scan_queue_item_by_scan_id(check_db, scan.id).queue_id == (
        queue_item.queue_id
    )
    check_db.close()


def test_heartbeat_survives_a_transient_database_error(monkeypatch):
    calls = []

    class StopAfterRetry:
        def wait(self, _timeout):
            return len(calls) >= 2

    def heartbeat(_db, _scan_id, _token):
        calls.append("beat")
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return True

    monkeypatch.setattr(
        worker, "get_session_factory", lambda: lambda: SimpleNamespace(close=lambda: None)
    )
    monkeypatch.setattr(worker, "heartbeat_scan_worker", heartbeat)
    worker._heartbeat_loop("scan-1", "token", StopAfterRetry())

    assert calls == ["beat", "beat"]


def test_runner_leaves_scan_status_to_the_worker_on_full_quit(monkeypatch, tmp_path):
    from scanner.pipeline import runner
    from scanner.runtime.worker_quit import FullApplicationQuitRequested

    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    statuses = []

    def quit_during_audio(*_args, **_kwargs):
        raise FullApplicationQuitRequested

    monkeypatch.setattr(runner, "require_available", lambda _name: None)
    monkeypatch.setattr(runner.settings, "frames_dir", tmp_path / "frames")
    monkeypatch.setattr(runner, "run_audio_stages", quit_during_audio)
    with pytest.raises(FullApplicationQuitRequested):
        runner.run_scan(
            db=object(),
            video_path=str(source),
            video_id="scan",
            video_duration_seconds=2.0,
            scan_video=False,
            status_updater=lambda _db, _scan, status: statuses.append(status),
            findings_writer=lambda *_args: None,
        )

    assert statuses == ["scanning"]
