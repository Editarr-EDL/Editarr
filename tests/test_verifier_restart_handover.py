"""The installed verifier waits for the listener a restart started (MU-45 fix 2).

A fake controller keeps its old ``http-child`` answering for a second after the 202,
then replaces it on the same port, as the packaged controller does on a failed bind.
"""

import json
import subprocess
import sys
from urllib.error import URLError

import pytest

from scripts import verify_installed_release as verifier

CHILD = """
import json, pathlib, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

port, marker, flag = int(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3])


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"marker": marker})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        flag.touch()
        self._send(202, {"target_generation": 2})

    def log_message(self, *_args):
        pass


HTTPServer.allow_reuse_address = True
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""

CONTROLLER = """
import pathlib, subprocess, sys, time

child_script, port, flag, mode = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3]), sys.argv[4]


def launch(marker):
    return subprocess.Popen(
        [sys.executable, child_script, port, marker, str(flag), "--internal-role", "http-child"]
    )


child = launch("old")
while not flag.exists():
    time.sleep(0.02)
if mode == "replace":
    time.sleep(1.0)
    child.terminate()
    child.wait()
    time.sleep(0.5)
    child = launch("new")
child.wait()
"""


@pytest.fixture
def controller(tmp_path):
    (tmp_path / "child.py").write_text(CHILD)
    (tmp_path / "controller.py").write_text(CONTROLLER)
    port = verifier._available_port()
    origin = f"http://127.0.0.1:{port}"
    started = []

    def start(mode):
        process = subprocess.Popen(
            [
                sys.executable,
                str(tmp_path / "controller.py"),
                str(tmp_path / "child.py"),
                str(port),
                str(tmp_path / "restart-requested"),
                mode,
            ],
            start_new_session=True,
        )
        started.append(process)
        verifier._wait_ready(origin, process)
        return process

    yield origin, start
    for process in started:
        verifier._stop(process)


def _marker(origin):
    return json.loads(verifier._request(origin + "/api/auth/status")[2])["marker"]


def test_readiness_after_a_same_origin_restart_comes_from_the_new_listener(controller):
    origin, start = controller
    process = start("replace")
    assert _marker(origin) == "old"

    accepted = verifier._restart_and_wait(origin, None, "csrf", process, origin)

    assert accepted["target_generation"] == 2
    assert _marker(origin) == "new"


def test_an_old_listener_that_never_stops_fails_the_restart(controller, monkeypatch):
    origin, start = controller
    process = start("stuck")
    monkeypatch.setattr(verifier, "_RESTART_TIMEOUT_SECONDS", 1.0)

    with pytest.raises(RuntimeError, match="previous HTTP listener did not stop"):
        verifier._restart_and_wait(origin, None, "csrf", process, origin)


def _answers(monkeypatch, *outcomes):
    calls = []

    def request(url, **_kwargs):
        calls.append(url)
        outcome = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(verifier, "_request", request)
    return calls


def test_sign_in_retries_a_connection_lost_during_the_handover(monkeypatch):
    calls = _answers(
        monkeypatch,
        ConnectionResetError("Connection reset by peer"),
        URLError(ConnectionRefusedError("Connection refused")),
        (200, {}, json.dumps({"csrf_token": "fresh"}).encode()),
    )

    _opener, csrf = verifier._login("http://127.0.0.1:1")

    assert csrf == "fresh"
    assert len(calls) == 3


def test_sign_in_rejected_by_the_new_listener_fails_at_once(monkeypatch):
    calls = _answers(monkeypatch, (401, {}, json.dumps({"detail": "invalid"}).encode()))

    with pytest.raises(RuntimeError, match="HTTP 401"):
        verifier._login("http://127.0.0.1:1")
    assert len(calls) == 1


def test_sign_in_that_never_connects_fails_at_the_restart_deadline(monkeypatch):
    monkeypatch.setattr(verifier, "_RESTART_TIMEOUT_SECONDS", 0.3)
    _answers(monkeypatch, ConnectionRefusedError("Connection refused"))

    with pytest.raises(RuntimeError, match="could not connect"):
        verifier._login("http://127.0.0.1:1")
