"""Explicit pausing retains safe checkpoints for later workers."""

from multiprocessing import get_context
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch

import pytest

from scanner import api, repository
from scanner.db import ScanRow
from scanner.models import FindingCreate
from scanner.persistence.repository.repository_checkpoints import commit_scan_stage_results
from scanner.persistence.repository.repository_queue import settle_stopped_scan_worker
from scanner.persistence.repository.scan_pause import pause_scan_queue_item
from scanner.persistence.write_lock import write_lock
from scanner.pipeline import runner, runner_scenes
from scanner.pipeline.scenes import Scene
from scanner.pipeline.visual_checkpoints import save_scene_checkpoint, scene_checkpoint_path
from scanner.runtime.process_control import WorkerIdentity
from scanner.runtime.worker_quit import ScanPauseRequested, ScanReservationLost
from scanner.security.scan_jobs.authority import JobGate
from tests.queue_support import _enqueue_scan, _queue_factory


def _finding(scan_id, start):
    return FindingCreate(
        video_id=scan_id, category="violence", subcategory="violence", start_seconds=start,
        end_seconds=start + 0.5, severity="medium", confidence=1.0, source="visual",
    )


def test_pausing_blocks_dispatch_until_worker_stops_and_preserves_checkpoint():
    with _queue_factory()() as db:
        scan, entry = _enqueue_scan(db, "paused")
        next_scan, _ = _enqueue_scan(db, "waiting")
        assert repository.reserve_scan_worker(db, scan.id, token="worker")
        assert repository.record_scan_worker_process(db, scan.id, token="worker", pid=42, pgid=42)
        repository.record_scan_stage(db, scan.id, "visual", "worker", "started", {})
        commit_scan_stage_results(db, scan.id, "worker", "visual", [_finding(scan.id, 3)])
        repository.update_scan_progress(db, scan.id, {"percent": 70, "stage": "visual"})

        assert pause_scan_queue_item(db, entry.queue_id) == "pausing"
        assert repository.get_active_scan_queue_item(db).id == scan.id
        assert repository.get_next_scan_queue(db).id == next_scan.id
        assert not repository.resume_orphaned_scan(db, entry.queue_id)
        repository.mark_orphaned_scan_workers(db, active_pids={42})
        assert db.get(ScanRow, scan.id).status == "pausing"
        settle_stopped_scan_worker(db, scan.id, token="older-worker")
        assert db.get(ScanRow, scan.id).status == "pausing"

        repository.mark_orphaned_scan_workers(db, active_pids=set())
        paused = repository.get_scan_queue_item_by_scan_id(db, scan.id)
        assert paused.status == "paused"
        assert paused.progress_percent == 70
        assert paused.scan_duration_as_of is None
        assert repository.get_active_scan_queue_item(db) is None
        assert repository.resume_orphaned_scan(db, entry.queue_id)
        assert repository.scan_checkpoint_stages(db, scan.id, require_provenance=True) == {"visual"}
        assert len(repository.get_scan_findings(db, scan.id).findings) == 1


def test_cancel_from_pausing_remains_cancelled_when_worker_exits():
    with _queue_factory()() as db:
        scan, entry = _enqueue_scan(db, "cancel-pausing")
        assert repository.reserve_scan_worker(db, scan.id, token="worker")
        assert pause_scan_queue_item(db, entry.queue_id) == "pausing"
        assert repository.cancel_scan_queue_item(db, entry.queue_id) == (scan.id, True)
        repository.mark_scan_orphaned(db, scan.id, "Old worker stopped")
        settle_stopped_scan_worker(db, scan.id, token="worker")
        assert db.get(ScanRow, scan.id).status == "cancelled"
        assert repository.get_scan_queue_item_by_scan_id(db, scan.id) is None


def test_scheduler_does_not_settle_pause_during_transient_ipc_loss(monkeypatch):
    factory = _queue_factory()
    with factory() as db:
        scan, entry = _enqueue_scan(db, "transient")
        next_scan, _ = _enqueue_scan(db, "queued")
        assert repository.reserve_scan_worker(db, scan.id, token="worker")
        assert repository.record_scan_worker_process(db, scan.id, token="worker", pid=42, pgid=42)
        assert pause_scan_queue_item(db, entry.queue_id) == "pausing"

    identity = WorkerIdentity(scan.id, 42, 42, "worker")
    proofs = iter([None, identity, None])
    liveness = iter([True, False])
    stop_event = Event()
    signalled = []
    cleaned = []

    def prove(*_args):
        with factory() as db:
            assert db.get(ScanRow, scan.id).status == "pausing"
        return next(proofs)

    def launch(scan_id):
        assert scan_id == next_scan.id
        stop_event.set()
        with factory() as db:
            assert db.get(ScanRow, scan.id).status == "paused"

    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", stop_event)
    monkeypatch.setattr(api, "_worker_identity", prove)
    monkeypatch.setattr(api, "_launch_scan_worker", launch)
    monkeypatch.setattr(api, "cleanup_worker_control_endpoint", lambda *_args: cleaned.append(1))
    monkeypatch.setattr(api.PROCESS_CONTROL, "request_stop", signalled.append)
    monkeypatch.setattr(api.PROCESS_CONTROL, "worker_may_be_running", lambda *_: next(liveness))

    api._scan_queue_worker()

    assert signalled == [identity]
    assert cleaned == [1]


def test_replaced_worker_reservation_fences_polling_and_writes():
    factory = _queue_factory()
    with factory() as db:
        scan, entry = _enqueue_scan(db, "fenced")
        assert repository.reserve_scan_worker(db, scan.id, token="old")
        gate = JobGate(factory, scan.id, worker_token="old")
        assert gate.require() is not None
        assert pause_scan_queue_item(db, entry.queue_id) == "pausing"
        settle_stopped_scan_worker(db, scan.id, token="old")
        assert repository.resume_orphaned_scan(db, entry.queue_id)
        assert repository.reserve_scan_worker(db, scan.id, token="new")
        with pytest.raises(ScanReservationLost):
            gate.require()
        with pytest.raises(ScanReservationLost):
            gate.run(db, lambda _session: pytest.fail("replaced worker was able to write"))


def test_incomplete_stage_commit_cannot_leave_findings_to_duplicate_on_retry():
    with _queue_factory()() as db:
        scan, _ = _enqueue_scan(db, "retry")
        with pytest.raises(ValueError, match="recorded provenance"), write_lock(db):
            commit_scan_stage_results(db, scan.id, "missing", "visual", [_finding(scan.id, 3)])
        assert not repository.get_scan_findings(db, scan.id).findings
        assert repository.scan_checkpoint_stages(db, scan.id) == set()


def _stall_checkpoint_publish(complete: Path, incomplete: Path, ready: Path) -> None:
    save_scene_checkpoint(complete, [_finding("scan", 0)])

    def before_replace(_source, destination):
        assert destination == incomplete
        ready.touch()
        sleep(30)
        raise TimeoutError("Checkpoint writer was not terminated")

    # The bytes exist in the synced temporary file as soon as replace is reached.
    with patch("scanner.runtime.durability.os.replace", before_replace):
        save_scene_checkpoint(incomplete, [_finding("scan", 10)])


def test_forced_worker_exit_replays_only_unpublished_scene(monkeypatch, tmp_path):
    scenes = [
        Scene(index=0, start_seconds=0, end_seconds=10),
        Scene(index=1, start_seconds=10, end_seconds=20),
    ]
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    complete, incomplete = [
        scene_checkpoint_path(checkpoints, scene, phase="complete") for scene in scenes
    ]
    ready = tmp_path / "before-replace"
    writer = get_context("spawn").Process(
        target=_stall_checkpoint_publish, args=(complete, incomplete, ready)
    )
    writer.start()
    try:
        deadline = monotonic() + 15
        while not ready.is_file():
            assert writer.is_alive() and monotonic() < deadline, "Checkpoint writer did not start"
            sleep(0.05)
    finally:
        if writer.is_alive():
            writer.kill()
        writer.join(timeout=5)
        returncode = writer.exitcode
        writer.close()
    assert returncode not in (None, 0)
    assert complete.is_file()
    assert not incomplete.exists()
    assert list(checkpoints.glob(f".{incomplete.name}-*"))

    replayed = []

    def sample(_video_path, scene, _directory, **_kwargs):
        replayed.append(scene.index)
        return [(tmp_path / "frame.jpg", scene.start_seconds)]

    monkeypatch.setattr(runner_scenes, "defer_descriptions", lambda *_: False)
    monkeypatch.setattr(runner, "get_video_frame_interval", lambda *_: 0.5)
    monkeypatch.setattr(runner, "sample_scene_frames", sample)
    monkeypatch.setattr(runner, "scan_frames", lambda frames, scan_id, **_: [
        _finding(scan_id, frames[0][1])
    ])
    monkeypatch.setattr(runner, "summarize_flagged_segments", lambda findings, *_, **_kw: findings)

    findings = runner._scan_visual_by_scene(
        "video.mkv", "scan", tmp_path / "retry", False, None,
        cached_scenes=scenes, checkpoint_dir=checkpoints,
    )

    assert replayed == [1]
    assert [finding.start_seconds for finding in findings] == [0, 10]
    assert incomplete.is_file()


@pytest.mark.parametrize("deferred", [False, True])
def test_visual_resume_reuses_detection_or_complete_scene_across_workspaces(
    monkeypatch, tmp_path, deferred,
):
    scenes = [Scene(index=index, start_seconds=index * 10, end_seconds=(index + 1) * 10)
              for index in range(3)]
    detected = []
    described = []
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    monkeypatch.setattr(runner_scenes, "defer_descriptions", lambda *_: deferred)
    monkeypatch.setattr(runner_scenes, "release_detector", lambda *_: None)
    monkeypatch.setattr(runner, "get_video_frame_interval", lambda *_: 0.5)

    def sample(_video, scene, directory, **_kwargs):
        directory.mkdir(parents=True, exist_ok=True)
        frame = directory / "frame.jpg"
        frame.write_bytes(b"fixture")
        return [(frame, scene.start_seconds)]

    def detect(frames, scan_id, **_kwargs):
        detected.append(frames[0][1])
        return [_finding(scan_id, frames[0][1])]

    def describe(findings, frames, **_kwargs):
        assert frames[0][0].is_file()
        described.append(findings[0].start_seconds)
        return [finding.model_copy(update={"details": "described"}) for finding in findings]

    monkeypatch.setattr(runner, "sample_scene_frames", sample)
    monkeypatch.setattr(runner, "scan_frames", detect)
    monkeypatch.setattr(runner, "summarize_flagged_segments", describe)

    def run(attempt, *, stop_on=None):
        def progress(_fraction, message):
            if message == stop_on:
                raise ScanPauseRequested

        return runner._scan_visual_by_scene(
            "video.mkv", "scan", tmp_path / attempt, False, None,
            cached_scenes=scenes, progress_callback=progress, checkpoint_dir=checkpoint_dir,
        )

    with pytest.raises(ScanPauseRequested):
        run("attempt-a", stop_on="Visual scan: scene 2/3 detected" if deferred
            else "Visual scan: scene 2/3 complete")
    assert detected == [0, 10]
    if deferred:
        assert not described
        with pytest.raises(ScanPauseRequested):
            run("attempt-b", stop_on="Visual summary: scene 1/3 complete")
        assert detected == [0, 10, 20]
        assert described == [0]

    findings = run("attempt-c")
    assert detected == described == [0, 10, 20]
    assert [(finding.start_seconds, finding.details) for finding in findings] == [
        (0, "described"), (10, "described"), (20, "described"),
    ]
