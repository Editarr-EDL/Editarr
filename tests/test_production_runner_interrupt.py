"""Interrupting the production browser runner retains no credential (w17 fix 1).

The real ``web/e2e/run-production-e2e.mjs`` and the real ``ProxyFixture`` log pump and
close run around a synthetic fixture readiness and a sleeping ``pnpm``, so only the
lifecycle is under test, not the installed app.
"""

import os
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSRF = "pSsyWIC1uAnajISq6_kzNwGZ0Uv1vVsdg31Wss_qAtw"
PASSWORD = "planted-fixture-password"
TRACE_PASSWORD = "planted-trace-password"

FIXTURE = f"""
import json, os, runpy, sys, time
from pathlib import Path

script, *arguments = sys.argv[1:]
if not script.endswith("production_browser_fixture.py"):
    sys.argv = [script, *arguments]
    runpy.run_path(script, run_name="__main__")
    raise SystemExit
sys.path.insert(0, {str(ROOT / "scripts")!r})
import proxy_fixture

proxy_fixture.shutil.which = lambda _name: "/usr/bin/true"
log_directory = Path(arguments[arguments.index("--log-dir") + 1])
fixture = proxy_fixture.ProxyFixture(
    "nginx", "editarr", None, log_directory=log_directory, label="interrupt"
)
fixture.backend = fixture._spawn(
    [
        sys.executable,
        "-c",
        "print('X-CSRF-Token: {CSRF}'); "
        "print('{{\\"password\\": \\"{PASSWORD}\\"}}')",
    ],
    "backend.log",
)
fixture.backend.wait(10)
try:
    print(json.dumps({{
        "base_url": fixture.origin, "prefix": "", "control_port": 1, "certificate_arg": "",
        "secondary_base_url": fixture.origin, "upstream_port": fixture.upstream_port,
        "proxy": "nginx",
    }}), flush=True)
    for _line in sys.stdin:
        pass
    marker = os.environ.get("TEARDOWN_MARKER")
    if marker:
        Path(marker).write_text("teardown")
        time.sleep(1)
finally:
    fixture.close()
"""

PNPM = """#!/bin/sh
echo $$ > "$PNPM_PID_FILE"
exec sleep 120
"""

PLAYWRIGHT = f"""
import json, os, signal, sys, time, zipfile
from pathlib import Path
signals = []
signal.signal(signal.SIGINT, lambda number, _frame: signals.append(number))
signal.signal(signal.SIGTERM, lambda number, _frame: signals.append(number))
Path(os.environ["PNPM_PID_FILE"]).write_text(str(os.getpid()))
while not signals:
    time.sleep(0.01)
time.sleep(2)
trace = Path(sys.argv[1]) / "interrupted-test" / "trace.zip"
trace.parent.mkdir(parents=True)
network = json.dumps({{"postData": {{"password": "{TRACE_PASSWORD}"}}}})
with zipfile.ZipFile(trace, "w") as archive:
    archive.writestr("trace.network", network)
sys.exit(130)
"""

# Like pnpm 11, this launcher forwards a signal to Playwright and exits at once, while
# Playwright keeps running and writes its trace after the fixture teardown would end.
FORWARDING_PNPM = f"""
import os, signal, subprocess, sys
playwright = None
def forward(number, _frame):
    if playwright is not None:
        playwright.send_signal(number)
    os._exit(128 + number)
signal.signal(signal.SIGINT, forward)
signal.signal(signal.SIGTERM, forward)
output = sys.argv[sys.argv.index("--output") + 1]
playwright = subprocess.Popen([sys.executable, "-c", {PLAYWRIGHT!r}, output])
playwright.wait()
"""


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _start_runner(tmp_path, pnpm_source, **environment):
    """Run the real runner with the synthetic fixture and ``pnpm``; return it and its paths."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the production runner")
    tools = tmp_path / "bin"
    tools.mkdir()
    python = tools / "fixture-python"
    python.write_text(f"#!{sys.executable}\n{FIXTURE}")
    pnpm = tools / "pnpm"
    pnpm.write_text(pnpm_source)
    for tool in (python, pnpm):
        tool.chmod(0o700)
    output = tmp_path / "output"
    workload_pid = tmp_path / "pnpm.pid"
    environment = {
        **os.environ,
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(ROOT),
        "PNPM_PID_FILE": str(workload_pid),
        "EDITARR_PRODUCTION_EDITARR": str(tmp_path / "editarr"),
        "EDITARR_PRODUCTION_PYTHON": str(python),
        "EDITARR_PRODUCTION_OUTPUT": str(output),
        **environment,
    }
    # A file, not a pipe: an orphaned workload would hold a pipe open after the runner exits.
    transcript = tmp_path / "runner.log"
    with transcript.open("wb") as transcript_sink:
        runner = subprocess.Popen(
            [node, str(ROOT / "web/e2e/run-production-e2e.mjs")],
            cwd=ROOT / "web",
            env=environment,
            stdout=transcript_sink,
            stderr=subprocess.STDOUT,
        )
    return runner, workload_pid, output, transcript


def _retained(output):
    """Every retained byte, with zip members (Playwright traces) read as stored."""
    chunks = []
    for path in output.rglob("*"):
        if path.is_file():
            chunks.append(path.read_bytes())
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as archive:
                    chunks.extend(archive.read(name) for name in archive.namelist())
    return b"".join(chunks)


def _stop(runner, workload):
    if runner.poll() is None:
        runner.kill()
        runner.wait()
    if workload is not None and _alive(workload):
        os.kill(workload, signal.SIGKILL)


@pytest.mark.parametrize("delivered", [signal.SIGINT, signal.SIGTERM])
def test_interrupted_runner_stops_the_workload_and_retains_no_secret(tmp_path, delivered):
    runner, workload_pid, output, transcript_file = _start_runner(tmp_path, PNPM)
    workload = None
    try:
        assert _wait_for(lambda: workload_pid.is_file() and workload_pid.read_text().strip())
        workload = int(workload_pid.read_text())
        runner.send_signal(delivered)
        runner.wait(timeout=60)
        transcript = transcript_file.read_text()
        logs = output / "fixture-logs"
        assert _wait_for(lambda: logs.is_dir() and any(logs.iterdir()), 10), transcript

        retained = _retained(output)
        assert CSRF.encode() not in retained
        assert PASSWORD.encode() not in retained
        assert retained.count(b"[REDACTED]") == 2
        assert runner.returncode == 128 + delivered, transcript
        assert not _alive(workload), "the runner stops its Playwright workload"
        assert '"remaining": []' in transcript, "the runner's final scan ran"
    finally:
        _stop(runner, workload)


def test_repeated_interrupts_still_wait_for_the_trace_and_scrub_it(tmp_path):
    marker = tmp_path / "teardown"
    runner, workload_pid, output, transcript_file = _start_runner(
        tmp_path, f"#!{sys.executable}\n{FORWARDING_PNPM}", TEARDOWN_MARKER=str(marker)
    )
    workload = None
    try:
        assert _wait_for(lambda: workload_pid.is_file() and workload_pid.read_text().strip())
        workload = int(workload_pid.read_text())
        runner.send_signal(signal.SIGINT)
        time.sleep(0.02)
        runner.send_signal(signal.SIGINT)
        assert _wait_for(marker.is_file, 10), "the fixture teardown started"
        runner.send_signal(signal.SIGTERM)
        runner.wait(timeout=60)
        transcript = transcript_file.read_text()

        assert runner.returncode == 128 + signal.SIGINT, transcript
        assert '"remaining": []' in transcript, "the runner's final scan ran"
        traces = list(output.rglob("trace.zip"))
        assert traces, "the runner waited for Playwright to write its trace"
        retained = _retained(output)
        for secret in (CSRF, PASSWORD, TRACE_PASSWORD):
            assert secret.encode() not in retained, secret
        assert not _alive(workload), "the runner stops its Playwright workload"
    finally:
        _stop(runner, workload)
