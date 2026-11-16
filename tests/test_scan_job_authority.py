"""A queued job runs only while its initiator may still run it (MU-21).

Decisions use the initiator's live account, capabilities, and media scope.
The scheduler refuses before launch, the worker refuses at
start, on polls, and before each findings write. A refused job ends `failed`,
leaves the queue, and writes nothing more.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import scanner.api as root
from scanner import worker
from scanner.db import ScanFindingRow
from scanner.modeling import model_acquisition_service
from scanner.modeling.model_acquisition_manifest import AcquisitionError
from scanner.modeling.model_acquisition_service import ModelAcquisitionService
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.identity import AccountStatus
from scanner.security.access.overrides import OverrideEffect
from scanner.security.scan_jobs import authority
from scanner.security.scan_jobs.authority import (
    REFUSAL_MESSAGES,
    JobAllowed,
    JobGate,
    JobRefusal,
    JobRefused,
    JobRefusedError,
    decide_job,
)
from tests.multi_user_access_support import OWNER, USER, live_accounts
from tests.scan_access_http import LIBRARY_B, ScanApp

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def app(tmp_path):
    accounts = live_accounts(tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    yield ScanApp(None, accounts, media)
    accounts.close()


def _decide(app: ScanApp, scan_id: str):
    with app.accounts.factory() as db:
        return decide_job(db, scan_id, now=NOW)


def test_a_queued_job_follows_its_initiators_live_permissions(app):
    scan_id, _ = app.seed_scan(USER, "user-job")
    assert isinstance(_decide(app, scan_id), JobAllowed)

    app.accounts.set_override(USER, C.SCANS_START, OverrideEffect.DENY)
    assert _decide(app, scan_id) == JobRefused(JobRefusal.CAPABILITY_DENIED)

    app.accounts.set_override(USER, C.SCANS_START, OverrideEffect.INHERIT)
    app.limit_to_library(USER, LIBRARY_B)
    assert _decide(app, scan_id) == JobRefused(JobRefusal.MEDIA_UNAVAILABLE)

    app.accounts.set_status(USER, AccountStatus.DISABLED)
    assert _decide(app, scan_id) == JobRefused(JobRefusal.ACCOUNT_UNAVAILABLE)


def test_every_active_initiator_runs_its_own_jobs(app):
    user_job, _ = app.seed_scan(USER, "user-job")
    owner_job, _ = app.seed_scan(OWNER, "owner-job", library=LIBRARY_B)

    assert isinstance(_decide(app, user_job), JobAllowed)
    assert isinstance(_decide(app, owner_job), JobAllowed)


def test_the_scheduler_ends_a_refused_job_and_launches_the_next(app, monkeypatch):
    root.SCAN_QUEUE_STOP_EVENT.clear()
    refused, _ = app.seed_scan(USER, "refused-job")
    allowed, allowed_queue = app.seed_scan(OWNER, "allowed-job")
    app.accounts.set_override(USER, C.SCANS_START, OverrideEffect.DENY)
    reserved: list[str] = []
    monkeypatch.setattr(root, "settings", SimpleNamespace(data_dir=app.media))
    monkeypatch.setattr(root, "get_session_factory", lambda: app.accounts.factory)
    monkeypatch.setattr(
        root, "reserve_scan_worker", lambda _db, scan_id, token: reserved.append(scan_id)
    )

    assert root._launch_scan_worker(refused) is False
    assert root._launch_scan_worker(allowed) is False

    row = app.scan(refused)
    assert (row.status, row.error_message) == (
        "failed",
        REFUSAL_MESSAGES[JobRefusal.CAPABILITY_DENIED],
    )
    assert app.queued() == [allowed]
    assert reserved == [allowed]


def test_the_gate_redecides_every_boundary_even_without_an_account_revision(app, monkeypatch):
    scan_id, _ = app.seed_scan(USER, "user-job")
    clock = [NOW]
    decisions: list[str] = []
    real = authority.decide_job

    def counted(db, job, **kwargs):
        decisions.append(job)
        return real(db, job, **kwargs)

    monkeypatch.setattr(authority, "decide_job", counted)
    gate = JobGate(app.accounts.factory, scan_id, clock=lambda: clock[0])

    assert isinstance(gate.require(), JobAllowed)
    clock[0] = NOW + timedelta(seconds=1)
    assert isinstance(gate.require(), JobAllowed)
    assert len(decisions) == 2

    clock[0] = NOW + timedelta(seconds=29)
    assert isinstance(gate.require(), JobAllowed)
    assert len(decisions) == 3

    app.accounts.set_override(USER, C.SCANS_START, OverrideEffect.DENY)
    with pytest.raises(JobRefusedError) as refused:
        gate.require()
    assert refused.value.refusal.reason is JobRefusal.CAPABILITY_DENIED


def _reserved_worker(app: ScanApp, monkeypatch, scan_id: str) -> None:
    from scanner import repository

    with app.accounts.factory() as db:
        assert repository.reserve_scan_worker(db, scan_id, token="worker-token")
        assert repository.record_scan_worker_process(
            db, scan_id, token="worker-token", pid=42, pgid=42
        )
    monkeypatch.setattr(worker, "settings", SimpleNamespace(data_dir=app.media))
    monkeypatch.setattr(worker, "require_supported_schema", lambda: "head")
    monkeypatch.setattr(worker, "require_owner_setup", lambda: None)
    monkeypatch.setattr(worker, "get_session_factory", lambda: app.accounts.factory)
    monkeypatch.setattr(worker, "_prevent_idle_system_sleep", _no_sleep_guard)


class _no_sleep_guard:
    def __enter__(self):
        return None

    def __exit__(self, *_exc):
        return None


def test_a_worker_refused_mid_run_writes_no_more_findings(app, monkeypatch):
    scan_id, _ = app.seed_scan(USER, "user-job")
    _reserved_worker(app, monkeypatch, scan_id)
    finding = SimpleNamespace()
    reached: list[str] = []

    def pipeline(db, _path, **kwargs):
        assert kwargs["should_cancel"]() is False
        reached.append("started")
        app.accounts.set_override(USER, C.SCANS_START, OverrideEffect.DENY)
        kwargs["findings_writer"](db, kwargs["video_id"], [finding])
        reached.append("wrote")

    monkeypatch.setattr(worker, "run_scan", pipeline)

    assert worker.run_scan_worker(scan_id, "worker-token") == 0

    row = app.scan(scan_id)
    assert row.status == "failed"
    assert row.error_message == REFUSAL_MESSAGES[JobRefusal.CAPABILITY_DENIED]
    assert row.worker_token is None
    assert reached == ["started"]
    assert app.queued() == []
    with app.accounts.factory() as db:
        assert db.query(ScanFindingRow).filter_by(scan_id=scan_id).count() == 0


def test_a_worker_refuses_at_start_before_reading_the_media(app, monkeypatch):
    scan_id, _ = app.seed_scan(USER, "user-job")
    _reserved_worker(app, monkeypatch, scan_id)
    app.limit_to_library(USER, LIBRARY_B)
    monkeypatch.setattr(worker, "run_scan", lambda *_args, **_kwargs: pytest.fail("ran"))

    assert worker.run_scan_worker(scan_id, "worker-token") == 0

    row = app.scan(scan_id)
    assert (row.status, row.error_message) == (
        "failed",
        REFUSAL_MESSAGES[JobRefusal.MEDIA_UNAVAILABLE],
    )
    assert app.queued() == []


def test_a_worker_without_download_permission_uses_installed_models_only(
    app, monkeypatch, tmp_path
):
    scan_id, _ = app.seed_scan(USER, "user-job")
    _reserved_worker(app, monkeypatch, scan_id)
    app.accounts.set_override(USER, C.MODELS_DOWNLOAD, OverrideEffect.DENY)
    # Recorded here so the worker's refusal is undone after the test.
    monkeypatch.setenv("EDITARR_WORKER_MODEL_DOWNLOADS", "allowed")
    service = ModelAcquisitionService(tmp_path / "models")
    errors: list[str] = []

    def pipeline(_db, _path, **_kwargs):
        try:
            service.acquire("faster-whisper-base")
        except AcquisitionError as error:
            errors.append(error.code)
            raise RuntimeError(f"Audio scan failed: {error.detail}") from error

    monkeypatch.setattr(worker, "run_scan", pipeline)

    assert worker.run_scan_worker(scan_id, "worker-token") == 0

    assert errors == ["download_not_permitted"]
    assert not (tmp_path / "models" / ".staging").exists()
    row = app.scan(scan_id)
    assert (row.status, row.error_message) == (
        "failed",
        REFUSAL_MESSAGES[JobRefusal.CAPABILITY_DENIED],
    )
    assert app.queued() == []
    assert model_acquisition_service._downloads_refused()
