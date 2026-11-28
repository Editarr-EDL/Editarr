"""Native subprocess evidence for token-bound worker process ownership."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from time import monotonic, sleep

from scanner.runtime import process_control
from scanner.runtime.process_control import ProcessControl
from tests.process_control_support import (
    _wait_for_identity,
    _worker_environment,
)


def test_cleanup_worker_control_endpoint_only_unlinks_its_own_socket(monkeypatch):
    if os.name != "posix":
        return
    # Keep the actual AF_UNIX path under the kernel limit even when pytest's
    # isolated TMPDIR itself is intentionally long.
    endpoint = Path("/tmp") / f"editarr-test-{uuid.uuid4().hex}.sock"
    monkeypatch.setattr(process_control, "worker_control_path", lambda *_args: endpoint)
    listener = socket.socket(socket.AF_UNIX)
    try:
        listener.bind(str(endpoint))
        process_control.cleanup_worker_control_endpoint("scan", "token")
        assert not endpoint.exists()
    finally:
        listener.close()

    try:
        endpoint.write_text("not a socket")
        process_control.cleanup_worker_control_endpoint("scan", "token")
        assert endpoint.read_text() == "not a socket"
    finally:
        endpoint.unlink(missing_ok=True)


def test_actual_subprocess_requires_token_bound_pid_proof_before_stop(tmp_path: Path):
    """A matching token endpoint cannot authorize a different/reused PID."""

    scan_id = str(uuid.uuid4())
    token = "test-token-" + uuid.uuid4().hex
    environment = _worker_environment(tmp_path)
    worker_program = """
from threading import Event
from scanner.runtime.process_control import WorkerControlServer, assign_current_worker_job
from pathlib import Path
import os
import sys
scan_id, token = sys.argv[1:]
stop = Event()
worker_job = None
try:
    worker_job = assign_current_worker_job(scan_id, token)
    server = WorkerControlServer(scan_id, token, stop)
    server.start()
except BaseException as error:
    if worker_job:
        worker_job.close()
    Path(os.environ['EDITARR_TEST_CONTROL_DIAGNOSTIC']).write_text(
        f"{type(error).__name__}: {error}", encoding='ascii', errors='replace'
    )
    raise
try:
    stop.wait(20)
finally:
    server.close()
    if worker_job:
        worker_job.close()
"""
    control = ProcessControl()
    worker = control.launch_worker(
        [sys.executable, "-c", worker_program, scan_id, token],
        scan_id,
        token,
        environment=environment,
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        identity = _wait_for_identity(
            control,
            scan_id,
            worker.pid,
            token,
            diagnostic_path=tmp_path / "control-diagnostic.txt",
        )
        # Same endpoint/token but a different PID is rejected: a PID reuse never
        # obtains authority to receive signals intended for an old worker.
        assert control.identity(scan_id, unrelated.pid, unrelated.pid, token) is None
        assert unrelated.poll() is None
        assert control.request_stop(identity)
        assert control.wait_for_exit(identity, 3)
        assert unrelated.poll() is None
    finally:
        for process in (worker, unrelated):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)


def test_actual_subprocess_force_stop_uses_platform_owned_process_tree(tmp_path: Path):
    """The Windows path reaches TerminateJobObject; POSIX reaches its owned group."""

    scan_id = str(uuid.uuid4())
    token = "test-token-" + uuid.uuid4().hex
    environment = _worker_environment(tmp_path)
    child_ready = tmp_path / "child-pid.txt"
    worker_program = """
import os
import subprocess
import sys
from pathlib import Path
from threading import Event
from scanner.runtime.process_control import WorkerControlServer, assign_current_worker_job

scan_id, token, child_ready = sys.argv[1:]
stop = Event()
worker_job = None
child = None
try:
    worker_job = assign_current_worker_job(scan_id, token)
    if os.name == "nt":
        # Use the base interpreter to avoid recording the PID of a venv redirector.
        child = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(20)"])
        Path(child_ready).write_text(str(child.pid), encoding="ascii")
    server = WorkerControlServer(scan_id, token, stop)
    server.start()
except BaseException as error:
    if child:
        child.terminate()
        child.wait(timeout=3)
    if worker_job:
        worker_job.close()
    Path(os.environ['EDITARR_TEST_CONTROL_DIAGNOSTIC']).write_text(
        f"{type(error).__name__}: {error}", encoding='ascii', errors='replace'
    )
    raise
try:
    stop.wait(20)
finally:
    server.close()
    if child:
        child.terminate()
        child.wait(timeout=3)
    if worker_job:
        worker_job.close()
"""
    control = ProcessControl()
    worker = control.launch_worker(
        [sys.executable, "-c", worker_program, scan_id, token, str(child_ready)],
        scan_id,
        token,
        environment=environment,
    )
    try:
        identity = _wait_for_identity(
            control,
            scan_id,
            worker.pid,
            token,
            diagnostic_path=tmp_path / "control-diagnostic.txt",
        )
        endpoint = process_control.worker_control_path(scan_id, token)
        if os.name == "posix":
            assert isinstance(endpoint, Path) and endpoint.exists()
        else:
            assert isinstance(endpoint, str) and endpoint.startswith(r"\\.\pipe\EditarrWorker-")
            child_pid = int(child_ready.read_text(encoding="ascii"))
            assert control.worker_may_be_running(identity.pid, None)
            assert control.worker_may_be_running(child_pid, None)
        assert control.force_stop(identity)
        worker.wait(timeout=3)
        if isinstance(endpoint, Path):
            assert not endpoint.exists()
        else:
            # TerminateJobObject must drain the child too. A closed worker pipe
            # alone is insufficient liveness evidence during pause recovery.
            deadline = monotonic() + 3
            while any(
                control.worker_may_be_running(pid, None) for pid in (identity.pid, child_pid)
            ):
                assert monotonic() < deadline, "Owned Windows worker tree did not exit"
                sleep(0.05)
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=3)
