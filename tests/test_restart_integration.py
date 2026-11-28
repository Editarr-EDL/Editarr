"""Controller and restart API integration tests."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scanner import repository
from scanner.db import ScanRow
from scanner.runtime.process_control import ProcessControl
from tests.restart_support import _csrf_headers, _setup, _unused_port, _wait_for_generation
from tests.restart_support import standalone_process as standalone_process


def test_actual_cli_acknowledges_before_same_port_restart(standalone_process):
    port = _unused_port()
    process, client, _data, log_path = standalone_process(port)
    csrf = _setup(client, port)
    with httpx.Client(base_url=f"http://localhost:{port}", timeout=1) as anonymous:
        assert anonymous.post("/api/system/restart").status_code == 401
    assert client.get("/api/system/restart", headers=_csrf_headers(port, csrf)).json()[
        "generation"
    ] == 1
    assert client.post(
        "/api/system/restart",
        headers={"Origin": f"http://localhost:{port}", "X-CSRF-Token": "invalid"},
    ).status_code == 403
    assert client.get("/api/system/restart", headers=_csrf_headers(port, csrf)).json()[
        "generation"
    ] == 1
    response = client.post("/api/system/restart", headers=_csrf_headers(port, csrf))
    # httpx has consumed the final response body before returning. The controller
    # cannot receive its restart commit until the outer ASGI send completes it.
    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "target_generation": 2,
        "reconnect_url": f"http://localhost:{port}/#/settings/general",
    }
    assert _wait_for_generation(client, 2, "applied")["supported"]
    assert process.poll() is None, log_path.read_text()


def test_actual_cli_rebinds_saved_port_and_preserves_worker(standalone_process):
    old_port, new_port = _unused_port(), _unused_port()
    controller, client, data, _log = standalone_process(old_port)
    csrf = _setup(client, old_port)
    engine = create_engine(f"sqlite:///{data / 'scanner.db'}")
    db = sessionmaker(bind=engine)()
    media = repository.upsert_media_item(
        db, server_id="http://jellyfin.test", jellyfin_id="movie-1",
        library_id="movies", item_type="Movie", name="Example", path="/example.mkv",
    )
    scan = repository.create_scan(
        db, media_item=media, resolved_local_path="/example.mkv", duration_seconds=60,
        scan_audio=True, audio_model="whisper", audio_filter="better_profanity",
        scan_video=True, video_model="nudenet", frame_interval=2, speed_up=False,
    )
    repository.enqueue_scan(db, scan.id)
    token = "rp08-worker-continuity"
    assert repository.reserve_scan_worker(db, scan.id, token=token)
    worker_environment = os.environ | {
        "HOME": str(data.parent / f"home-{old_port}"),
        "TMPDIR": str(data.parent / f"runtime-{old_port}"),
        "PYTHONPATH": str(Path(__file__).parents[1]),
    }
    worker_program = """
from threading import Event
from time import sleep
from scanner.runtime.process_control import WorkerControlServer, assign_current_worker_job
import sys
scan_id, token = sys.argv[1:]
stop = Event()
worker_job = assign_current_worker_job(scan_id, token)
server = WorkerControlServer(scan_id, token, stop)
server.start()
try:
    stop.wait(30)
finally:
    server.close()
    sleep(0.25)  # Keep the process live briefly after its authenticated endpoint closes.
    if worker_job:
        worker_job.close()
"""
    worker_control = ProcessControl()
    worker = worker_control.launch_worker(
        [sys.executable, "-c", worker_program, scan.id, token],
        scan.id,
        token,
        environment=worker_environment,
    )
    identity = worker_control.discover_identity(
        scan.id,
        token,
        launcher_pid=worker.pid,
        process_group=worker.pid if os.name == "posix" else None,
    )
    assert identity is not None
    assert repository.record_scan_worker_process(
        db, scan.id, token=token, pid=identity.pid, pgid=identity.process_group
    )
    db.close()
    try:
        assert client.put(
            "/api/settings/deployment", headers=_csrf_headers(old_port, csrf),
            json={"port": new_port},
        ).status_code == 200
        accepted = client.post(
            "/api/system/restart", headers=_csrf_headers(old_port, csrf)
        )
        assert accepted.status_code == 202
        assert accepted.json()["reconnect_url"] == (
            f"http://localhost:{new_port}/#/settings/general"
        )
        next_client = httpx.Client(
            base_url=f"http://localhost:{new_port}", cookies=client.cookies, timeout=1
        )
        assert _wait_for_generation(next_client, 2, "applied")["generation"] == 2
        assert worker.poll() is None
        check = sessionmaker(bind=engine)()
        row = check.get(ScanRow, scan.id)
        assert row is not None and row.status == "scanning"
        assert row.worker_pid == identity.pid
        assert row.progress_message == "Reattached to active scan"
        check.close()
        saved = json.loads((data / "deployment.json").read_text())
        assert saved["port"] == new_port and "base_url" not in saved
    finally:
        if controller.poll() is None:
            if os.name == "nt":
                # Windows SIGTERM terminates a process abruptly, so it cannot
                # demonstrate the controller's signal-handler Full Quit path.
                # Invoke the existing routine in an isolated interpreter with
                # the same runtime configuration, then stop the controller.
                quit_environment = os.environ | {
                    "EDITARR_DATA_DIR": str(data),
                    "EDITARR_DATABASE_URL": f"sqlite:///{data / 'scanner.db'}",
                    "EDITARR_FRAMES_DIR": str(data / "frames"),
                    "EDITARR_EXPORTS_DIR": str(data / "exports"),
                    "EDITARR_QWEN_REPLAYS_DIR": str(data / "qwen-replays"),
                    "HOME": str(data.parent / f"home-{old_port}"),
                    "TMPDIR": str(data.parent / f"runtime-{old_port}"),
                    "PYTHONPATH": str(Path(__file__).parents[1]),
                }
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "from scanner.runtime.restart_service import _full_quit_owned_workers; "
                        "_full_quit_owned_workers()",
                    ],
                    cwd=Path(__file__).parents[1],
                    env=quit_environment,
                    check=True,
                    timeout=15,
                )
                assert worker_control.wait_for_exit(identity, 3)
                controller.terminate()
            else:
                os.kill(controller.pid, signal.SIGTERM)
                # Pytest owns this synthetic worker, so reap it as the native
                # worker's parent or init would during application Quit.
                assert worker.wait(timeout=12) == 0
            controller.wait(timeout=15)
            check = sessionmaker(bind=engine)()
            row = check.get(ScanRow, scan.id)
            assert row is not None and row.status == "orphaned"
            assert repository.get_scan_queue_item_by_scan_id(check, scan.id) is not None
            check.close()
        engine.dispose()
        if worker.poll() is None:
            os.kill(worker.pid, signal.SIGTERM)
            worker.wait(timeout=3)


def test_cli_port_override_masks_saved_port_across_restart(standalone_process):
    port, pending = _unused_port(), _unused_port()
    _process, client, data, _log = standalone_process(port, cli_override=True)
    csrf = _setup(client, port)
    assert client.put(
        "/api/settings/deployment", headers=_csrf_headers(port, csrf), json={"port": pending}
    ).status_code == 200
    accepted = client.post("/api/system/restart", headers=_csrf_headers(port, csrf))
    assert accepted.status_code == 202
    assert accepted.json()["reconnect_url"] == f"http://localhost:{port}/#/settings/general"
    _wait_for_generation(client, 2, "applied")
    assert json.loads((data / "deployment.json").read_text())["port"] == pending


def test_occupied_target_recovers_old_listener_without_activating_pending(standalone_process):
    port, occupied_port = _unused_port(), _unused_port()
    _process, client, data, _log = standalone_process(port, explicit_http_cookie=True)
    csrf = _setup(client, port)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", occupied_port))
        occupied.listen()
        assert client.put(
            "/api/settings/deployment", headers=_csrf_headers(port, csrf),
            json={
                "port": occupied_port,
                "base_url": f"http://localhost:{port}/pending",
            },
        ).status_code == 200
        assert client.post(
            "/api/system/restart", headers=_csrf_headers(port, csrf)
        ).status_code == 202
        status = _wait_for_generation(client, 2, "recovered")
        assert "Saved changes remain pending" in status["detail"]
        stored = json.loads((data / "deployment.json").read_text())
        assert stored["port"] == occupied_port
        assert stored["cookie_scope"]["activated_base_url"] == f"http://localhost:{port}"
        assert stored["cookie_scope"]["pending_previous_path"] == "/"
        assert "not_before" not in stored["cookie_scope"]
        assert client.get("/api/system/restart").status_code == 200


def test_successful_prefix_activation_invalidates_old_forms_session(standalone_process):
    port = _unused_port()
    _process, client, data, _log = standalone_process(port, explicit_http_cookie=True)
    csrf = _setup(client, port)
    assert client.put(
        "/api/settings/deployment", headers=_csrf_headers(port, csrf),
        json={"base_url": f"http://localhost:{port}/editarr"},
    ).status_code == 200
    assert client.post(
        "/api/system/restart", headers=_csrf_headers(port, csrf)
    ).status_code == 202
    prefixed = httpx.Client(
        base_url=f"http://localhost:{port}/editarr", cookies=client.cookies, timeout=1
    )
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            response = prefixed.get("/api/system/restart")
            if response.status_code == 401:
                break
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    else:
        pytest.fail("old forms session remained valid after prefix activation")
    scope = json.loads((data / "deployment.json").read_text())["cookie_scope"]
    assert scope["not_before"] and scope["activated_base_url"].endswith("/editarr")
