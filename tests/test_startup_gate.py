"""Lifecycle checks for authentication-gated scan recovery and scheduling."""

from __future__ import annotations

from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import scanner.http.auth_api as auth_api
from scanner import api
from scanner.db import Base, get_db
from scanner.http.auth_api import router
from scanner.settings.config import Settings
from tests.jellyfin_setup_fake import install, setup_body


class _DummyDb:
    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def regeneration_worker(monkeypatch):
    worker = SimpleNamespace(start=Mock(), stop=Mock(return_value=True))
    factory = Mock(return_value=worker)
    monkeypatch.setattr("scanner.runtime.export_regeneration.ExportRegenerationScheduler", factory)
    monkeypatch.setattr(api.app.state, "export_regeneration_scheduler", None, raising=False)
    return factory, worker


def _configure_startup(monkeypatch, tmp_path, *, setup_required: bool, calls: list[str]) -> None:
    from scanner.persistence.migrations import preflight

    monkeypatch.setattr(preflight, "database_location", lambda: (tmp_path / "scanner.db", tmp_path))
    configuration = SimpleNamespace(
        data_dir=tmp_path,
        exports_dir=tmp_path / "exports",
        log_dir=tmp_path / "logs",
    )
    monkeypatch.setattr(api, "settings", configuration)
    monkeypatch.setattr(
        api, "load_csrf_encryption_key", lambda _path: SimpleNamespace(key_id="key")
    )
    monkeypatch.setattr(api, "load_api_key_wrap_key", lambda _path: SimpleNamespace())
    monkeypatch.setattr(api, "seal_plaintext_api_keys", lambda _db, _key: 0)
    monkeypatch.setattr(
        api,
        "AuthenticationAuditLogger",
        lambda _path: SimpleNamespace(emit=lambda **_: None),
    )
    monkeypatch.setattr(api, "init_db", lambda: calls.append("schema"))
    monkeypatch.setattr(api, "get_session_factory", lambda: _DummyDb)
    monkeypatch.setattr(api, "run_audit_retention", lambda _db: calls.append("audit-retention"))
    monkeypatch.setattr(
        api,
        "record_key_change_revocation",
        lambda _db, count: calls.append(f"audit-revoked-{count}"),
    )
    monkeypatch.setattr(
        api,
        "revoke_sessions_with_other_csrf_key_id",
        lambda _db, _key_id, **_: calls.append("key-revoke") or 0,
    )
    monkeypatch.setattr(api, "cleanup_expired_sessions", lambda _db: calls.append("cleanup") or 0)
    monkeypatch.setattr(
        api,
        "setup_required",
        lambda _db: calls.append("administrator") or setup_required,
    )
    monkeypatch.setattr(
        api, "recover_cancelled_scan_queue_items", lambda _db: calls.append("recover") or 0
    )
    monkeypatch.setattr(api, "recover_interrupted_credits_model_acquisitions", lambda _db: 0)
    monkeypatch.setattr(api, "recover_terminal_scan_queue_items", lambda _db: 0)
    monkeypatch.setattr(api, "scanning_scan_worker_processes", lambda _db: [])
    monkeypatch.setattr(
        api, "mark_orphaned_scan_workers", lambda _db, _pids: calls.append("orphans") or []
    )
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: calls.append("start"))
    monkeypatch.setattr(api, "SCAN_QUEUE_PROCESSING_ACTIVE", False)
    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", Event())
    monkeypatch.setattr(api, "SCAN_QUEUE_WAKE_EVENT", Event())


def test_startup_performs_auth_maintenance_but_gates_queue_before_setup(
    monkeypatch, tmp_path, regeneration_worker
):
    calls: list[str] = []
    _configure_startup(monkeypatch, tmp_path, setup_required=True, calls=calls)

    api.startup()

    assert calls == ["schema", "audit-retention", "key-revoke", "cleanup", "administrator"]
    assert not api.SCAN_QUEUE_PROCESSING_ACTIVE
    regeneration_worker[0].assert_not_called()


def test_startup_audits_sessions_revoked_by_a_replaced_csrf_key(monkeypatch, tmp_path):
    calls: list[str] = []
    _configure_startup(monkeypatch, tmp_path, setup_required=True, calls=calls)
    monkeypatch.setattr(api, "revoke_sessions_with_other_csrf_key_id", lambda _db, _key_id, **_: 3)

    api.startup()

    assert calls == ["schema", "audit-retention", "audit-revoked-3", "cleanup", "administrator"]


def test_startup_recovers_restore_before_deployment_resolution(monkeypatch, tmp_path):
    from scanner.backup import activation

    calls: list[str] = []
    _configure_startup(monkeypatch, tmp_path, setup_required=True, calls=calls)
    (tmp_path / activation.JOURNAL).write_text("pending")
    monkeypatch.setattr(
        activation,
        "recover_restore",
        lambda _database, _data: (
            calls.append("restore") or (tmp_path / activation.JOURNAL).unlink()
        ),
    )
    monkeypatch.setattr(api, "active_deployment_snapshot", lambda: calls.append("deployment"))
    api.startup()
    assert calls[:3] == ["restore", "deployment", "schema"]


def test_restored_generation_does_not_activate_scan_queue(monkeypatch, tmp_path):
    calls: list[str] = []
    _configure_startup(monkeypatch, tmp_path, setup_required=False, calls=calls)
    (tmp_path / "restore-reconciliation.json").write_text('{"queue_paused":true}')
    api.startup()
    assert calls == ["schema", "audit-retention", "key-revoke", "cleanup", "administrator"]
    assert not api.SCAN_QUEUE_PROCESSING_ACTIVE


def test_cli_preconfigured_startup_recovers_and_starts_once(
    monkeypatch, tmp_path, regeneration_worker
):
    calls: list[str] = []
    _configure_startup(monkeypatch, tmp_path, setup_required=False, calls=calls)

    api.startup()
    api._recover_and_start_scan_queue_processing()

    assert calls == [
        "schema",
        "audit-retention",
        "key-revoke",
        "cleanup",
        "administrator",
        "recover",
        "orphans",
        "start",
    ]
    assert api.SCAN_QUEUE_PROCESSING_ACTIVE
    factory, worker = regeneration_worker
    factory.assert_called_once_with(_DummyDb, data_dir=tmp_path)
    worker.start.assert_called_once_with()
    api.shutdown()
    worker.stop.assert_called_once_with()
    assert api.app.state.export_regeneration_scheduler is None


def test_queue_activation_serializes_concurrent_setup_completion(monkeypatch):
    entered_recovery = Event()
    release_recovery = Event()
    calls: list[str] = []

    def recover(_db):
        calls.append("recover")
        entered_recovery.set()
        assert release_recovery.wait(timeout=2)
        return 0

    monkeypatch.setattr(api, "get_session_factory", lambda: _DummyDb)
    monkeypatch.setattr(api, "recover_cancelled_scan_queue_items", recover)
    monkeypatch.setattr(api, "recover_interrupted_credits_model_acquisitions", lambda _db: 0)
    monkeypatch.setattr(api, "recover_terminal_scan_queue_items", lambda _db: 0)
    monkeypatch.setattr(api, "scanning_scan_worker_processes", lambda _db: [])
    monkeypatch.setattr(api, "mark_orphaned_scan_workers", lambda _db, _pids: [])
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: calls.append("start"))
    monkeypatch.setattr(api, "SCAN_QUEUE_PROCESSING_ACTIVE", False)
    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", Event())
    monkeypatch.setattr(api, "SCAN_QUEUE_WAKE_EVENT", Event())

    first = Thread(target=api._recover_and_start_scan_queue_processing)
    second = Thread(target=api._recover_and_start_scan_queue_processing)
    first.start()
    assert entered_recovery.wait(timeout=2)
    second.start()
    release_recovery.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert calls == ["recover", "start"]


def test_successful_api_setup_activates_once_and_failed_or_conflicting_setup_does_not(
    monkeypatch, tmp_path
):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    application = FastAPI()
    application.include_router(router)
    install(monkeypatch)
    activations: list[str] = []
    application.state.activate_scan_queue_processing = lambda: activations.append("start")

    def test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = test_db
    monkeypatch.setenv("EDITARR_AUTH_COOKIE_SECURE", "false")
    monkeypatch.setattr(auth_api, "settings", Settings(data_dir=tmp_path))
    try:
        with TestClient(application) as client:
            invalid = client.post("/api/auth/setup", json={"username": "Admin"})
            assert invalid.status_code == 422
            assert activations == []

            refused = client.post("/api/auth/setup", json=setup_body(password="wrong"))
            assert refused.status_code == 401
            assert activations == []
            setup = client.post("/api/auth/setup", json=setup_body())
            assert setup.status_code == 201
            assert client.get("/api/auth/status").status_code == 200
            conflict = client.post("/api/auth/setup", json=setup_body())
            assert conflict.status_code == 409
    finally:
        engine.dispose()

    assert activations == ["start"]


def test_shutdown_resets_activation_for_the_next_http_lifecycle(monkeypatch):
    monkeypatch.setattr(api, "SCAN_QUEUE_PROCESSING_ACTIVE", True)
    monkeypatch.setattr(api, "SCAN_QUEUE_STOP_EVENT", Event())
    monkeypatch.setattr(api, "SCAN_QUEUE_WAKE_EVENT", Event())
    monkeypatch.setattr(api, "SCAN_QUEUE_WORKER_THREAD", None)

    api.shutdown()

    assert not api.SCAN_QUEUE_PROCESSING_ACTIVE
    assert api.SCAN_QUEUE_STOP_EVENT.is_set()
