"""Revocation reaches a separately launched worker through its durable initiator."""

import os
import signal
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread

import pytest

from scanner import repository
from scanner.db import ScanFindingRow
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import USER
from tests.scan_job_boundary_support import synthetic_video as synthetic_video
from tests.test_scan_access_http import app as app


@pytest.mark.parametrize(
    "mode", ["control", "revoked_empty", "revoked_finding", "revoked_download"]
)
def test_separate_worker_rechecks_live_authority(app, synthetic_video, mode):
    scan_id, _ = app.seed_scan(USER, "separate-process")
    Path(app.scan(scan_id).resolved_local_path).write_bytes(synthetic_video.read_bytes())
    with app.accounts.factory() as db:
        assert repository.reserve_scan_worker(db, scan_id, token="boundary-worker-token")
    data = app.accounts.configuration.data_dir
    checkout = Path(__file__).resolve().parents[1]
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("EDITARR_")
    }
    environment.update(
        EDITARR_DATA_DIR=str(data),
        EDITARR_LOG_DIR=str(data / "child-logs"),
        EDITARR_DATABASE_URL=f"sqlite:///{app.accounts.path}",
        PYTHONPATH=str(checkout),
        PYTHONDONTWRITEBYTECODE="1",
    )
    output = (
        "empty"
        if mode == "revoked_empty"
        else "download"
        if mode == "revoked_download"
        else "finding"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.scan_job_boundary_support",
            scan_id,
            "boundary-worker-token",
            output,
        ],
        env=environment,
        cwd=checkout,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    ready = Event()
    lines = []

    def collect():
        for line in process.stdout:
            lines.append(line)
            if "DETECTOR_READY" in line:
                ready.set()

    reader = Thread(target=collect, daemon=True)
    reader.start()
    try:
        assert ready.wait(30), "Worker did not reach detector: " + "".join(lines)
        if mode != "control":
            capability = C.MODELS_DOWNLOAD if mode == "revoked_download" else C.SCANS_START
            app.accounts.set_override(USER, capability, OverrideEffect.DENY)
        process.stdin.write("continue\n")
        process.stdin.flush()
        assert process.wait(timeout=30) == 0
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        reader.join(timeout=5)
        process.stdin.close()
        process.stdout.close()
    row = app.scan(scan_id)
    with app.accounts.factory() as db:
        findings = db.query(ScanFindingRow).filter_by(scan_id=scan_id).count()
    assert row.initiator_id == USER and row.worker_token is None
    assert app.queued() == []
    assert row.status == ("complete" if mode == "control" else "failed"), "".join(lines)
    assert findings == (1 if mode == "control" else 0)
    if mode == "revoked_download":
        assert not list(data.rglob("*.complete.json"))
