"""Controller and restart API integration tests."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scanner.http.restart_api import router
from scanner.models import RestartCapability
from scanner.runtime import restart_service
from scanner.runtime.restart_service import (
    RestartClient,
    _activate_application,
    _ReadinessGate,
    _recover_or_raise,
)
from scanner.settings.deployment_config import (
    CookieScope,
    DeploymentStore,
    resolve_deployment_snapshot,
)
from tests.operations_access_support import as_owner
from tests.restart_support import _snapshot
from tests.restart_support import standalone_process as standalone_process


def test_activation_records_baseline_then_advances_cutoff_only_for_explicit_scope(tmp_path):
    store = DeploymentStore(tmp_path)
    baseline = store.activate(_snapshot(tmp_path))
    assert baseline.session_not_before is None
    assert store.read().cookie_scope.activated_local_default

    store.update(base_url="https://editarr.example/media", cookie_scope=CookieScope(
        activated_base_url=baseline.base_url,
        activated_local_default=True,
        pending_previous_path="/",
    ))
    explicit = resolve_deployment_snapshot(store=store, environment={})
    # Merely resolving/saving never activates the cutoff.
    assert explicit.session_not_before is None
    activated = store.activate(explicit)
    assert activated.session_not_before is not None
    assert activated.legacy_cookie_paths == ("/",)


def test_restart_client_deduplicates_and_marks_controller_loss():
    class Connection:
        def __init__(self):
            self.sent = []
        def send(self, value):
            self.sent.append(value)
        def poll(self, _timeout):
            return True
        def recv(self):
            return ("reserved",)

    capability = RestartCapability(
        supported=True, mode="standalone_controller", generation=3,
        in_progress=False, outcome="idle",
    )
    connection = Connection()
    client = RestartClient(connection, capability)
    assert client.reserve() is None
    assert client.reserve() == "restart_in_progress"
    assert capability.target_generation == 4
    assert connection.sent == [("reserve",)]

    client.cancel()
    connection.poll = lambda _timeout: False
    assert client.reserve() == "restart_unavailable"
    assert not capability.supported
    assert not capability.in_progress
    assert capability.reason == "controller_unavailable"


def test_queue_startup_failure_does_not_activate_pending_session_scope(tmp_path):
    from scanner.cli import ServerLaunchContext

    store = DeploymentStore(tmp_path)
    baseline = store.activate(_snapshot(tmp_path))
    store.update(base_url="https://editarr.example/pending")
    pending = resolve_deployment_snapshot(store=store, environment={})
    context = ServerLaunchContext("127.0.0.1", None, True, pending, ("--api-only",))

    def fail_queue():
        raise RuntimeError("queue database unavailable")

    application = FastAPI()
    application.state.setup_required = False
    application.state.activate_scan_queue_processing = fail_queue
    before = store.read()
    with pytest.raises(RuntimeError, match="queue database unavailable"):
        _activate_application(application, context, store, recovering=False)
    assert store.read() == before
    assert store.read().cookie_scope.activated_base_url == baseline.base_url
    assert store.read().cookie_scope.not_before is None


@pytest.mark.parametrize("send_fails", [False, True])
def test_restart_commit_waits_for_response_delivery_and_cancels_on_failure(send_fails):
    events = []

    async def application(scope, receive, send):
        scope["editarr.restart_after_response"] = lambda: events.append("commit")
        scope["editarr.restart_cancel_response"] = lambda: events.append("cancel")
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b"accepted"})

    async def send(message):
        assert "commit" not in events
        if message["type"] == "http.response.body":
            if send_fails:
                raise OSError("connection closed")
            events.append("delivered")

    gate = _ReadinessGate(application)
    gate.ready = True
    invocation = gate({"type": "http"}, None, send)
    if send_fails:
        with pytest.raises(OSError, match="connection closed"):
            asyncio.run(invocation)
        assert events == ["cancel"]
    else:
        asyncio.run(invocation)
        assert events == ["delivered", "commit"]


def test_direct_app_reports_restart_unsupported(monkeypatch, tmp_path):
    import scanner.http.deployment_api as deployment_api
    import scanner.http.restart_api as restart_api

    configuration = SimpleNamespace(data_dir=tmp_path)
    monkeypatch.setattr(deployment_api, "settings", configuration)
    monkeypatch.setattr(restart_api, "_store", lambda: DeploymentStore(tmp_path))
    application = FastAPI()
    application.state.deployment_snapshot = _snapshot(tmp_path)
    application.state.launch_context = SimpleNamespace(host="127.0.0.1", cli_port_override=None)
    application.include_router(router)
    as_owner(application, tmp_path / "access").close()
    with TestClient(application) as client:
        status = client.get("/api/system/restart")
        restart = client.post("/api/system/restart")
    assert status.status_code == 200
    assert status.json()["reason"] == "manual_uvicorn"
    assert restart.status_code == 409
    assert restart.json()["code"] == "restart_unsupported"


def test_development_reload_reports_accurate_restart_limit(monkeypatch, tmp_path):
    import scanner.http.deployment_api as deployment_api
    import scanner.http.restart_api as restart_api

    configuration = SimpleNamespace(data_dir=tmp_path)
    monkeypatch.setattr(deployment_api, "settings", configuration)
    monkeypatch.setattr(restart_api, "_store", lambda: DeploymentStore(tmp_path))
    application = FastAPI()
    application.state.deployment_snapshot = _snapshot(tmp_path)
    application.state.restart_capability = RestartCapability(
        supported=False, mode="unsupported", generation=0, in_progress=False,
        outcome="idle", reason="development_reload",
    )
    application.include_router(router)
    as_owner(application, tmp_path / "access").close()
    with TestClient(application) as client:
        status = client.get("/api/system/restart")
        restart = client.post("/api/system/restart")
    assert status.json()["reason"] == "development_reload"
    assert restart.status_code == 409
    assert "development reload" in restart.json()["detail"]


def test_supported_api_deduplicates_before_first_response_commit(monkeypatch, tmp_path):
    import scanner.http.restart_api as restart_api

    class Client:
        def __init__(self):
            self.capability = RestartCapability(
                supported=True, mode="standalone_controller", generation=1,
                in_progress=False, outcome="idle",
            )
            self.reservations = 0

        def reserve(self):
            if self.capability.in_progress:
                return "restart_in_progress"
            self.reservations += 1
            self.capability.in_progress = True
            self.capability.target_generation = 2
            return None

        def commit(self):
            raise AssertionError("plain TestClient does not run the child send callback")

        def cancel(self):
            raise AssertionError("the response did not fail")

    controller = Client()
    snapshot = _snapshot(tmp_path)
    monkeypatch.setattr(restart_api, "_next_start_snapshot", lambda *_: snapshot)
    monkeypatch.setattr(restart_api, "_store", lambda: SimpleNamespace(read=lambda: object()))
    application = FastAPI()
    application.state.restart_client = controller
    application.include_router(router)
    as_owner(application, tmp_path / "access").close()
    with TestClient(application) as api:
        first = api.post("/api/system/restart")
        duplicate = api.post("/api/system/restart")
    assert first.status_code == 202
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "restart_in_progress"
    assert controller.reservations == 1


def test_recovery_failure_has_actionable_manual_diagnostic():
    with pytest.raises(RuntimeError, match="previous listener recovery also failed") as raised:
        _recover_or_raise(object(), True)
    assert "deployment.json" in str(raised.value)


def test_controller_reports_initial_readiness_timeout_without_a_wall_clock_wait(monkeypatch):
    class Connection:
        def __init__(self):
            self.sent = []
            self.closed = False

        def send(self, value):
            self.sent.append(value)

        def poll(self, _timeout):
            return False

        def close(self):
            self.closed = True

    class Endpoint:
        def __init__(self):
            self.connection = Connection()
            self.closed = False

        def accept(self, _timeout):
            return self.connection

        def close(self):
            self.closed = True

    class Process:
        def poll(self):
            return None

    endpoint = Endpoint()
    process = Process()
    stopped = []
    context = SimpleNamespace(
        host="127.0.0.1",
        cli_port_override=None,
        api_only=True,
        snapshot=object(),
        original_argv=(),
    )
    monkeypatch.setattr(restart_service, "STARTUP_TIMEOUT", 0)
    monkeypatch.setattr(restart_service, "_HttpChildEndpoint", lambda: endpoint)
    monkeypatch.setattr(restart_service, "_launch_http_child", lambda _endpoint: process)
    monkeypatch.setattr(restart_service, "_stop_child", lambda child: stopped.append(child))
    monkeypatch.setattr(restart_service, "_full_quit_owned_workers", lambda: None)

    with pytest.raises(RuntimeError, match="HTTP readiness timed out"):
        restart_service.run_controller(context)

    assert endpoint.connection.sent[0][0] == "start"
    assert endpoint.connection.closed
    assert endpoint.closed
    assert stopped == [process, process]


