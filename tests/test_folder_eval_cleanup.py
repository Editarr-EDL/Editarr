"""Backends are closed, and their models and servers released, on every exit path."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
import types

import pytest

from scanner.evaluation.folder_eval import backends, runner
from scanner.evaluation.folder_eval.backends import BackendUnavailable
from tests.test_folder_eval_runner import _runner_layout


class _Backend:
    model_id = "fake"

    def __init__(self, events, fail=None, where="score"):
        self.events, self.fail, self.where = events, fail, where

    def meta(self):
        if self.where == "meta" and self.fail:
            raise self.fail
        return {"model_id": "fake", "supports": ["violence"], "thresholds": {"*": 0.5}}

    def score_frames(self, paths):
        if self.where == "score" and self.fail:
            raise self.fail
        return [{"scores": {"violence": 0.1}} for _ in paths]

    def close(self):
        self.events.append("closed")


@pytest.mark.parametrize(
    ("fail", "where"),
    [
        (None, "score"),
        (BackendUnavailable("gone"), "score"),
        (KeyboardInterrupt(), "score"),
        (SystemExit(143), "score"),
        (RuntimeError("meta broke"), "meta"),
    ],
)
def test_run_model_closes_the_backend_on_every_exit(tmp_path, monkeypatch, fail, where):
    layout = _runner_layout(tmp_path)
    events = []
    monkeypatch.setattr(runner, "create_backend", lambda _id: _Backend(events, fail, where))
    if fail is None:
        runner.run_model(layout, "fake")
    else:
        with pytest.raises(type(fail)):
            runner.run_model(layout, "fake")
    assert events == ["closed"]


def test_all_closes_each_model_before_loading_the_next(tmp_path, monkeypatch):
    from scanner.commands import folder_eval_cli as cli

    _runner_layout(tmp_path)
    events = []

    def create(model_id):
        events.append(f"load {model_id}")
        backend = _Backend(events)
        backend.close = lambda: events.append(f"close {model_id}")
        return backend

    monkeypatch.setenv("EDITARR_WORKER_MODEL_DOWNLOADS", "")
    monkeypatch.setattr(backends, "_downloads_allowed", False)
    monkeypatch.setattr(runner, "create_backend", create)
    for name in ("write_inventory", "extract_frames", "summarize", "install_viewer"):
        monkeypatch.setattr(cli, name, lambda *_a, **_k: None)
    assert cli.main(["--data-root", str(tmp_path), "all", "--models", "a,b"]) == 0
    assert events == ["load a", "close a", "load b", "close b"]


def test_create_backend_closes_a_backend_that_fails_its_check(monkeypatch):
    events = []

    class Missing:
        def check_available(self):
            raise BackendUnavailable("unavailable: no weights")

        def close(self):
            events.append("closed")

    monkeypatch.setattr(backends, "_decision_backends", lambda: {"m": Missing})
    with pytest.raises(BackendUnavailable):
        backends.create_backend("m")
    assert events == ["closed"]


def test_cascade_closes_backends_it_builds_for_meta_and_checks():
    from scanner.evaluation.folder_eval.decision.cascade import CascadeBackend

    events = []

    def part(name, fail=False):
        class Part:
            def meta(self):
                return {"runtime": name}

            def check_available(self):
                if fail:
                    raise BackendUnavailable(f"unavailable: {name}")

            def close(self):
                events.append(f"close {name}")

        return Part

    cascade = CascadeBackend(decider=part("decider"), describer=part("describer", fail=True))
    cascade.meta()
    with pytest.raises(BackendUnavailable):
        cascade.check_available()
    assert events == ["close decider", "close decider", "close describer"]


def test_local_backends_release_cached_models(monkeypatch):
    cleared = []
    moderation = types.SimpleNamespace(_CACHE={"encoder": object()}, _LOAD_LOCK=_Lock())
    visual = types.SimpleNamespace(_detector=object())
    torch = types.SimpleNamespace(
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: True)),
        mps=types.SimpleNamespace(empty_cache=lambda: cleared.append("mps")),
    )
    monkeypatch.setitem(sys.modules, "scanner.pipeline.siglip2_moderation", moderation)
    monkeypatch.setitem(sys.modules, "scanner.pipeline.visual", visual)
    monkeypatch.setitem(sys.modules, "torch", torch)
    backend = object.__new__(backends.Siglip2Backend)
    backend.close()
    assert moderation._CACHE == {} and visual._detector is None and cleared == ["mps"]


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_sigterm_stops_a_running_llama_server(tmp_path):
    """A SIGTERM during a run stops the llama-server child instead of orphaning it."""
    child = textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        from scanner.commands.folder_eval_cli import _sigterm_exits
        from scanner.evaluation.folder_eval.decision.llama_server import LlamaServerProcess

        health = (
            "import http.server, sys\\n"
            "class H(http.server.BaseHTTPRequestHandler):\\n"
            "    def do_GET(self):\\n"
            "        self.send_response(200); self.end_headers()\\n"
            "    def log_message(self, *a): pass\\n"
            "http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()\\n"
        )
        log = Path({str(tmp_path)!r}) / "s.log"
        server = LlamaServerProcess(lambda port: [sys.executable, "-c", health, str(port)], log)
        with _sigterm_exits():
            try:
                server.ensure_started()
                print(server._process.pid, flush=True)
                time.sleep(60)
            finally:
                server.close()
    """)
    process = subprocess.Popen(
        [sys.executable, "-c", child], stdout=subprocess.PIPE, text=True, cwd=os.getcwd()
    )
    server_pid = int(process.stdout.readline())
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=30) == 128 + signal.SIGTERM
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(server_pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(server_pid, signal.SIGKILL)
    pytest.fail("llama-server survived SIGTERM")


def test_backend_closes_only_after_in_flight_scoring_ends_when_a_write_fails(
    tmp_path, monkeypatch
):
    import json
    import threading

    from scanner.evaluation.folder_eval.paths import Layout
    from tests.folder_eval_support import clip

    layout = Layout(tmp_path)
    items = [clip(f"i{n}", "video/x", [], frames=1) for n in range(6)]
    for item in items:
        target = layout.results / item["frames"][0]["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    layout.inventory.write_text(json.dumps({"items": items}), encoding="utf-8")
    lock = threading.Lock()
    state = {"started": 0, "finished": 0, "in_flight": 0, "at_close": None}

    class Slow:
        model_id = "fake"
        default_concurrency = 3

        def meta(self):
            return {"model_id": "fake", "thresholds": {"*": 0.5}}

        def score_frames(self, paths):
            with lock:
                state["started"] += 1
                state["in_flight"] += 1
            time.sleep(0.01 if paths[0].parent.name == "i0" else 0.4)
            with lock:
                state["in_flight"] -= 1
                state["finished"] += 1
            return [{"scores": {}}]

        def close(self):
            with lock:
                state["at_close"] = (state["in_flight"], state["started"], state["finished"])

    def failing_append(_descriptor, _record):
        raise OSError("disk full")

    monkeypatch.setattr(runner, "create_backend", lambda _id: Slow())
    monkeypatch.setattr(runner, "append_line", failing_append)
    with pytest.raises(OSError, match="disk full"):
        runner.run_model(layout, "fake", concurrency=3)
    in_flight, started, finished = state["at_close"]
    assert in_flight == 0 and started == finished
    assert started < len(items)  # queued items were cancelled, not scored
