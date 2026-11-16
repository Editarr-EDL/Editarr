"""Live initiator authority fences worker writes and optional model acquisition."""

from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event, Thread

import pytest

import scanner.api as root
from scanner import worker
from scanner.db import ScanFindingRow, ScanRow, UserRow
from scanner.http import credits_acquisition, credits_api
from scanner.modeling import model_acquisition_authority, model_acquisition_transfer
from scanner.pipeline import runner
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.identity import AccountStatus
from scanner.security.access.overrides import OverrideEffect
from scanner.security.access.vocabulary import Role
from scanner.security.scan_jobs import authority
from scanner.security.scan_jobs.authority import JobGate, JobRefusedError
from tests.multi_user_access_support import USER
from tests.scan_access_http import LIBRARY_B
from tests.scan_job_boundary_support import finding, prepare_worker
from tests.scan_job_boundary_support import synthetic_video as synthetic_video
from tests.test_model_acquisition import _PAYLOADS, FakeRepository, _service
from tests.test_scan_access_credits_http import _waiting_credits_job
from tests.test_scan_access_http import app as app


def revoke(app, change):
    if change == "disabled":
        app.accounts.set_status(USER, AccountStatus.DISABLED)
    elif change == "capability":
        app.accounts.set_override(USER, C.SCANS_START, OverrideEffect.DENY)
    elif change == "role_policy":
        app.accounts.set_role_default(Role.USER, C.SCANS_START, False)
    elif change == "models":
        app.accounts.set_override(USER, C.MODELS_DOWNLOAD, OverrideEffect.DENY)
    else:
        app.limit_to_library(USER, LIBRARY_B)


@pytest.mark.parametrize("with_findings", [False, True])
@pytest.mark.parametrize("change", ["disabled", "capability", "media", "role_policy"])
def test_worker_refuses_results_after_detector_revocation(
    app, monkeypatch, synthetic_video, change, with_findings
):
    scan_id = prepare_worker(app, monkeypatch, synthetic_video)

    def detector(*_args, **_kwargs):
        revoke(app, change)
        return [finding(scan_id)] if with_findings else []

    monkeypatch.setattr(runner, "_scan_visual_isolated", detector)
    assert worker.run_scan_worker(scan_id, "worker-token") == 0
    row = app.scan(scan_id)
    with app.accounts.factory() as db:
        assert db.query(ScanFindingRow).filter_by(scan_id=scan_id).count() == 0
    assert row.initiator_id == USER
    assert row.status == "failed" and row.worker_token is None
    assert app.queued() == []


def test_worker_control_persists_findings(app, monkeypatch, synthetic_video):
    scan_id = prepare_worker(app, monkeypatch, synthetic_video)
    monkeypatch.setattr(
        runner, "_scan_visual_isolated", lambda *_args, **_kwargs: [finding(scan_id)]
    )
    assert worker.run_scan_worker(scan_id, "worker-token") == 0
    with app.accounts.factory() as db:
        assert db.query(ScanFindingRow).filter_by(scan_id=scan_id).count() == 1
    assert app.scan(scan_id).status == "complete"
    assert app.queued() == []


def test_model_download_denial_reaches_the_next_acquisition(
    app, monkeypatch, synthetic_video, tmp_path
):
    scan_id = prepare_worker(app, monkeypatch, synthetic_video)
    transport = FakeRepository()
    service = _service(tmp_path, transport)

    def detector(*_args, **kwargs):
        revoke(app, "models")
        assert kwargs["should_cancel"]() is False
        service.acquire("fake-model")
        return [finding(scan_id)]

    monkeypatch.setattr(runner, "_scan_visual_isolated", detector)
    assert worker.run_scan_worker(scan_id, "worker-token") == 0
    assert transport.calls == []
    assert app.scan(scan_id).status == "failed"
    assert app.queued() == []


@pytest.mark.parametrize("change", ["disabled", "capability", "media", "models"])
def test_credits_acquisition_rechecks_before_start(app, monkeypatch, tmp_path, change):
    scan_id = _waiting_credits_job(app, USER)
    with app.accounts.factory() as db:
        db.get(ScanRow, scan_id).status = "acquiring_model"
        db.commit()
    revoke(app, change)
    transport = FakeRepository()
    service = _service(tmp_path, transport)
    monkeypatch.setattr(root, "get_acquisition_service", lambda: service)
    monkeypatch.setattr(root, "get_session_factory", lambda: app.accounts.factory)
    credits_api._run_credits_model_acquisition(JobGate(app.accounts.factory, scan_id), "fake-model")
    assert transport.calls == []
    assert app.scan(scan_id).status == "failed"
    assert app.queued() == []


def test_credits_revocation_during_fetch_never_publishes_ready(app, monkeypatch, tmp_path):
    scan_id = _waiting_credits_job(app, USER)

    class RevokingRepository(FakeRepository):
        def fetch(self, *args, **kwargs):
            revoke(app, "disabled")
            return super().fetch(*args, **kwargs)

    service = _service(tmp_path, RevokingRepository())
    monkeypatch.setattr(root, "get_acquisition_service", lambda: service)
    monkeypatch.setattr(root, "get_session_factory", lambda: app.accounts.factory)
    credits_api._run_credits_model_acquisition(JobGate(app.accounts.factory, scan_id), "fake-model")
    assert service.status("fake-model").state != "ready"
    assert app.scan(scan_id).status == "failed"
    assert app.queued() == []


def test_credits_acquisition_control_reaches_ready(app, monkeypatch, tmp_path):
    scan_id = _waiting_credits_job(app, USER)
    app.accounts.set_override(USER, C.MODELS_DOWNLOAD, OverrideEffect.ALLOW)
    transport = FakeRepository()
    service = _service(tmp_path, transport)
    monkeypatch.setattr(root, "get_acquisition_service", lambda: service)
    monkeypatch.setattr(root, "get_session_factory", lambda: app.accounts.factory)
    monkeypatch.setattr(root, "_start_scan_queue_worker_if_needed", lambda: None)
    credits_api._run_credits_model_acquisition(JobGate(app.accounts.factory, scan_id), "fake-model")
    assert transport.calls
    assert service.status("fake-model").state == "ready"
    assert app.scan(scan_id).status == "queued"
    assert len(app.queued()) == 1


def test_credits_chunks_do_not_each_take_a_writer_lock(app, monkeypatch, tmp_path):
    scan_id = _waiting_credits_job(app, USER)
    chunks, locks = [], []

    class ChunkedRepository(FakeRepository):
        def fetch(self, artifact, artifact_file, destination, *, progress, cancelled):
            self.calls.append(artifact_file.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as handle:
                for byte in _PAYLOADS[artifact_file.path]:
                    assert not cancelled()
                    handle.write(bytes([byte]))
                    progress(1)
                    chunks.append(1)

    real_lock = authority.write_lock

    @contextmanager
    def counted_lock(db):
        locks.append(1)
        with real_lock(db):
            yield

    app.accounts.set_override(USER, C.MODELS_DOWNLOAD, OverrideEffect.ALLOW)
    service = _service(tmp_path, ChunkedRepository())
    monkeypatch.setattr(authority, "write_lock", counted_lock)
    monkeypatch.setattr(authority, "monotonic", lambda: 1.0)
    monkeypatch.setattr(credits_acquisition, "monotonic", lambda: 1.0)
    monkeypatch.setattr(root, "get_acquisition_service", lambda: service)
    monkeypatch.setattr(root, "get_session_factory", lambda: app.accounts.factory)
    monkeypatch.setattr(root, "_start_scan_queue_worker_if_needed", lambda: None)
    credits_api._run_credits_model_acquisition(JobGate(app.accounts.factory, scan_id), "fake-model")
    assert len(chunks) > 1000
    assert len(locks) == 4
    assert service.status("fake-model").state == "ready"
    assert app.scan(scan_id).status == "queued"


def test_old_model_directory_is_removed_after_the_writer_lock(app, monkeypatch, tmp_path):
    import sqlite3

    scan_id = _waiting_credits_job(app, USER)
    app.accounts.set_override(USER, C.MODELS_DOWNLOAD, OverrideEffect.ALLOW)
    gate = JobGate(app.accounts.factory, scan_id)
    service = _service(tmp_path, FakeRepository())
    final = service._artifact_dir(service.manifest.artifact("fake-model"))
    final.mkdir(parents=True)
    (final / "old-model").write_bytes(b"old artifact")
    real_remove, removed = model_acquisition_transfer.shutil.rmtree, []
    with app.accounts.factory() as db:
        database = db.get_bind().url.database

    def remove(path, *args, **kwargs):
        if ".replaced-" in path.name:
            with sqlite3.connect(database, timeout=0) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            removed.append(path)
        return real_remove(path, *args, **kwargs)

    monkeypatch.setattr(model_acquisition_transfer.shutil, "rmtree", remove)
    with model_acquisition_authority.scoped_job_acquisition(gate):
        service.acquire("fake-model")
    assert len(removed) == 1
    assert not removed[0].exists()
    assert service.status("fake-model").state == "ready"


def test_boundary_decides_after_waiting_for_a_revoking_writer(app, monkeypatch):
    scan_id, _ = app.seed_scan(USER, "locked-write")
    gate = JobGate(app.accounts.factory, scan_id, clock=lambda: datetime.now(UTC))
    gate.require()
    waiting, finished = Event(), Event()
    observed = []
    real_lock = authority.write_lock

    @contextmanager
    def announced_lock(db):
        waiting.set()
        with real_lock(db):
            yield

    def write():
        try:
            with app.accounts.factory() as db:
                gate.run(db, lambda _db: observed.append("unauthorized write"))
        except JobRefusedError:
            observed.append("refused")
        finally:
            finished.set()

    monkeypatch.setattr(authority, "write_lock", announced_lock)
    with app.accounts.factory() as db, real_lock(db):
        db.get(UserRow, USER).status = "disabled"
        db.flush()
        thread = Thread(target=write)
        thread.start()
        try:
            assert waiting.wait(5)
            assert not finished.wait(0.05)
        finally:
            db.commit()
            thread.join(timeout=10)
    assert finished.is_set()
    assert observed == ["refused"]
