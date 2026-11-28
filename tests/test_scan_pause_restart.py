"""The controller can pause an older live worker and resume its job after a cold start."""

import json
import os
import subprocess
import sys
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scanner import repository
from scanner.db import ScanRow
from scanner.runtime.process_control import ProcessControl
from tests.restart_support import _csrf_headers, _setup, _unused_port, standalone_process
from tests.scan_access_support import owner_caller

__all__ = ["standalone_process"]

_WORKER = """
import json
import os
from pathlib import Path
import sys
from threading import Event
from scanner.runtime.process_control import WorkerControlServer, assign_current_worker_job

scan_id, token, ready = sys.argv[1:]
stop = Event()
job = assign_current_worker_job(scan_id, token)
server = WorkerControlServer(scan_id, token, stop)
server.start()
try:
    ready = Path(ready)
    marker = ready.with_suffix('.tmp')
    marker.write_text(json.dumps([os.getpid(), os.getpgrp() if os.name == 'posix' else None]))
    marker.replace(ready)
    # A worker launched by an older version only understands authenticated Quit.
    if not stop.wait(15):
        raise RuntimeError('No authenticated stop request')
finally:
    server.close()
    if job:
        job.close()
"""


def _queue_item(client, queue_id):
    response = client.get("/api/scans/queue")
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["queue_id"] == queue_id)


def test_paused_worker_checkpoint_survives_cold_restart(standalone_process):
    port = _unused_port()
    controller, client, data, _log = standalone_process(port, native=True)
    csrf = _setup(client, port)
    headers = _csrf_headers(port, csrf)
    database = data / "scanner.db"
    ready = data / "synthetic-worker.json"
    environment = os.environ | {
        "EDITARR_DATA_DIR": str(data),
        "EDITARR_DATABASE_URL": f"sqlite:///{database}",
        "EDITARR_LOG_DIR": str(data / "logs"),
        "EDITARR_CACHE_DIR": str(data / "cache"),
        "TMPDIR": str(data.parent / f"runtime-{port}"),
    }
    engine = create_engine(f"sqlite:///{database}")
    factory = sessionmaker(bind=engine)
    worker = None
    try:
        with factory() as db:
            source = str(data / "placeholder.mkv")
            media = repository.upsert_media_item(
                db, server_id="http://jellyfin.test", jellyfin_id="movie-1",
                library_id="movies", item_type="Movie", name="Example", path=source,
            )
            scan = repository.create_scan(
                db, media_item=media, resolved_local_path=source, duration_seconds=60,
                scan_audio=False, audio_model=None, audio_filter=None,
                scan_video=True, video_model="nudenet", initiator=owner_caller(db).initiator,
            )
            token = str(uuid4())
            assert repository.reserve_scan_worker(db, scan.id, token=token)
            repository.record_scan_stage(db, scan.id, "visual", token, "started", {})
            repository.mark_scan_checkpoint(db, scan.id, "visual", execution_id=token)
            control = ProcessControl()
            worker = control.launch_worker(
                [sys.executable, "-c", _WORKER, scan.id, token, str(ready)],
                scan.id, token, environment=environment,
            )
            deadline = monotonic() + 8
            while not ready.is_file():
                assert worker.poll() is None and monotonic() < deadline, "Worker did not start"
                sleep(0.05)
            pid, pgid = json.loads(ready.read_text())
            assert repository.record_scan_worker_process(
                db, scan.id, token=token, pid=pid, pgid=pgid,
            )
            queue_id = repository.enqueue_scan(db, scan.id).queue_id

        route = f"/api/scans/queue/{queue_id}"
        response = client.post(f"{route}/pause", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"paused": False, "pausing": True}
        assert worker.wait(timeout=8) == 0
        deadline = monotonic() + 6
        while _queue_item(client, queue_id)["status"] != "paused":
            if monotonic() >= deadline:
                pytest.fail("Queue did not settle the stopped worker as paused")
            sleep(0.05)

        subprocess.run(
            [sys.executable, "-m", "scanner.cli", "--data-dir", str(data), "lifecycle", "quit"],
            env=environment, check=True, timeout=15,
        )
        controller.wait(timeout=15)
        restarted, after, _, _ = standalone_process(port, reopen=True, native=True)
        after.cookies.update(client.cookies)
        assert restarted.pid != controller.pid
        assert _queue_item(after, queue_id)["status"] == "paused"
        with factory() as db:
            assert db.get(ScanRow, scan.id).worker_pid is None
            assert repository.scan_checkpoint_stages(db, scan.id, require_provenance=True) == {
                "visual"
            }
        resumed = after.post(f"{route}/resume", headers=headers)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json() == {"resumed": True}
    finally:
        engine.dispose()
        if worker is not None and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=3)
