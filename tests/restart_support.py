"""Controller and restart API integration tests."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from scanner.settings.deployment_config import DeploymentStore, resolve_deployment_snapshot
from tests.jellyfin_setup_fake import serve, setup_body


def _snapshot(tmp_path, **updates):
    snapshot = resolve_deployment_snapshot(store=DeploymentStore(tmp_path), environment={})
    return replace(snapshot, **updates)


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def standalone_process(tmp_path: Path):
    """Launch an isolated CLI controller and clean up its exact child processes."""
    processes: list[subprocess.Popen[bytes]] = []

    clients: list[httpx.Client] = []

    def launch(
        port: int, *, cli_override: bool = False, explicit_http_cookie: bool = False
    ):
        data = tmp_path / f"service-{port}"
        home = tmp_path / f"home-{port}"
        runtime_temp = tmp_path / f"runtime-{port}"
        data.mkdir()
        home.mkdir()
        runtime_temp.mkdir()
        if not cli_override:
            (data / "deployment.json").write_text(
                json.dumps({"version": 1, "port": port}), encoding="utf-8"
            )
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("EDITARR_")
        }
        environment.update(
            {
                "EDITARR_DATA_DIR": str(data),
                "EDITARR_DATABASE_URL": f"sqlite:///{data / 'scanner.db'}",
                "EDITARR_FRAMES_DIR": str(data / "frames"),
                "EDITARR_EXPORTS_DIR": str(data / "exports"),
                "EDITARR_QWEN_REPLAYS_DIR": str(data / "qwen-replays"),
                # RuntimePaths derives native log/cache roots from the process home.
                # The actual controller and its restarted HTTP children must not write
                # diagnostics or model/cache state under the developer's home.
                "HOME": str(home),
                "TMPDIR": str(runtime_temp),
                "PYTHONPATH": str(Path(__file__).parents[1]),
            }
        )
        if explicit_http_cookie:
            environment["EDITARR_AUTH_COOKIE_SECURE"] = "false"
        command = [sys.executable, "-m", "scanner.cli", "--api-only"]
        if cli_override:
            command += ["--port", str(port)]
        log_path = data / "service.log"
        log = log_path.open("wb")
        process = subprocess.Popen(
            command,
            cwd=Path(__file__).parents[1],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log.close()
        processes.append(process)
        client = httpx.Client(base_url=f"http://localhost:{port}", timeout=1)
        clients.append(client)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f"standalone startup failed: {log_path.read_text()}")
            try:
                if client.get("/api/auth/status").status_code == 200:
                    return process, client, data, log_path
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        pytest.fail(f"standalone startup timed out: {log_path.read_text()}")

    yield launch
    for client in clients:
        client.close()
    for process in processes:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=3)


def _setup(client: httpx.Client, port: int) -> str:
    with serve() as (_fake, server_url):
        response = client.post(
            "/api/auth/setup",
            headers={"Origin": f"http://localhost:{port}"},
            json=setup_body(server_url=server_url),
        )
    assert response.status_code == 201, response.text
    return response.json()["csrf_token"]


def _csrf_headers(port: int, csrf: str) -> dict[str, str]:
    return {"Origin": f"http://localhost:{port}", "X-CSRF-Token": csrf}


def _wait_for_generation(client: httpx.Client, generation: int, outcome: str) -> dict:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            response = client.get("/api/system/restart")
            if response.status_code == 200:
                result = response.json()
                if result["generation"] >= generation and result["outcome"] == outcome:
                    return result
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    pytest.fail(f"restart did not reach generation {generation} with {outcome}")


