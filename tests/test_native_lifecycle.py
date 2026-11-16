"""NATIVE-06 unit coverage for private native launcher coordination."""

from __future__ import annotations

import logging
import os
import socket
import sys
from pathlib import Path
from threading import Event, Thread

import pytest

from scanner import cli
from scanner.runtime import native_lifecycle, restart_service
from scanner.runtime.native_lifecycle import (
    InstanceMetadata,
    NativeInstance,
    NativeLifecycleAdapter,
)
from scanner.runtime.runtime_logging import configure_runtime_logging, redirect_standard_streams
from scanner.runtime.runtime_paths import RuntimePaths


def _paths(tmp_path: Path) -> RuntimePaths:
    return RuntimePaths(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        log_dir=tmp_path / "logs",
        cache_dir=tmp_path / "cache",
        temp_dir=tmp_path / "tmp",
        resources_dir=tmp_path / "resources",
    )


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_first_and_second_launch_share_one_authenticated_controller_and_browser_action(tmp_path):
    opened: list[str] = []
    adapter = NativeLifecycleAdapter(browser_opener=lambda url: opened.append(url) or True)
    owner = NativeInstance(_paths(tmp_path), adapter)
    assert owner.claim_or_open_existing("https://editarr.example/editarr/", open_ui=True)

    second = NativeInstance(_paths(tmp_path), adapter)
    assert not second.claim_or_open_existing("https://wrong.example/", open_ui=True)
    # The second launch arrived before readiness, so only the owner opens once
    # after the real controller signal.
    owner.ready("https://editarr.example/editarr/", open_ui=True)
    assert opened == ["https://editarr.example/editarr/"]

    third = NativeInstance(_paths(tmp_path), adapter)
    assert not third.claim_or_open_existing("https://wrong.example/", open_ui=True)
    assert opened == ["https://editarr.example/editarr/", "https://editarr.example/editarr/"]
    # The controller reports readiness again after an in-web HTTP-generation
    # restart. That updates the active URL but is not a new application launch.
    owner.ready("https://editarr.example/new-prefix/", open_ui=True)
    assert opened == ["https://editarr.example/editarr/", "https://editarr.example/editarr/"]
    assert third.status()["url"] == "https://editarr.example/new-prefix/"
    owner.close()


def test_stale_and_pid_reuse_state_recovers_without_signaling_recorded_process(
    monkeypatch, tmp_path
):
    paths = _paths(tmp_path)
    stale = NativeInstance(paths)
    stale.directory.mkdir(parents=True, exist_ok=True)
    stale.lock_path.write_text("99999", encoding="ascii")
    stale.state_path.write_bytes(
        InstanceMetadata(
            99999,
            str(tmp_path / "missing.sock"),
            "A" * 43,
            "http://localhost:8000/",
            True,
        ).encode()
    )
    monkeypatch.setattr(native_lifecycle, "INSTANCE_START_TIMEOUT", 0)
    monkeypatch.setattr(os, "kill", lambda *_args: (_ for _ in ()).throw(AssertionError()))

    assert stale.claim_or_open_existing("http://localhost:8123/", open_ui=False)
    assert stale.owns_instance
    stale.close()


def test_unresponsive_live_owner_lock_is_never_removed_or_stolen(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    owner = NativeInstance(paths)
    assert owner._try_acquire_lock()
    owner.state_path.write_text("corrupt", encoding="utf-8")
    monkeypatch.setattr(native_lifecycle, "INSTANCE_START_TIMEOUT", 0)

    contender = NativeInstance(paths)
    try:
        with pytest.raises(native_lifecycle.NativeLifecycleError):
            contender.claim_or_open_existing("http://localhost:8123/", open_ui=False)
        assert owner.owns_instance
        assert owner.lock_path.exists()
    finally:
        owner.close()


def test_control_actions_require_authenticated_endpoint_and_quit_never_targets_a_pid(tmp_path):
    paths = _paths(tmp_path)
    opened: list[str] = []
    owner = NativeInstance(
        paths, NativeLifecycleAdapter(browser_opener=lambda url: opened.append(url) or True)
    )
    assert owner.claim_or_open_existing("http://localhost:8123/app/", open_ui=False)
    owner.ready("http://localhost:8123/app/", open_ui=False)

    client = NativeInstance(paths)
    status = client.status()
    assert status == {
        "ok": True,
        "pid": os.getpid(),
        "url": "http://localhost:8123/app/",
        "ready": True,
    }
    assert client.open_ui()
    assert opened == ["http://localhost:8123/app/"]
    assert client.quit()
    assert owner.quit_event.wait(1)
    owner.close()


def test_control_refuses_state_endpoint_not_derived_from_its_capability(monkeypatch, tmp_path):
    instance = NativeInstance(_paths(tmp_path))
    instance.directory.mkdir(parents=True, exist_ok=True)
    instance.state_path.write_bytes(
        InstanceMetadata(
            os.getpid(),
            str(tmp_path / "attacker-controlled.sock"),
            "A" * 43,
            "http://localhost:8123/",
            True,
        ).encode()
    )
    monkeypatch.setattr(
        native_lifecycle,
        "connect_private_endpoint",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not connect")),
    )

    assert instance.status() is None


def test_listener_survives_windows_style_permission_error(tmp_path):
    class Connection:
        def recv(self):
            return "status"

        def send(self, response):
            responses.append(response)
            instance._closed.set()

        def close(self):
            return None

    class Listener:
        def __init__(self):
            self.calls = 0

        def accept(self):
            self.calls += 1
            if self.calls == 1:
                raise PermissionError("wrong named-pipe authentication")
            return Connection()

    responses: list[dict[str, object]] = []
    instance = NativeInstance(_paths(tmp_path))
    instance._metadata = InstanceMetadata(
        os.getpid(), "pipe", "token", "http://localhost:8123/", True
    )
    listener = Listener()
    instance._listener = listener

    instance._serve()

    assert listener.calls == 2
    assert responses == [
        {
            "ok": True,
            "pid": os.getpid(),
            "url": "http://localhost:8123/",
            "ready": True,
        }
    ]


def test_native_log_rotation_redacts_credentials_and_creates_private_files(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    monkeypatch.setattr("scanner.runtime.runtime_logging._LOG_BYTES", 80)
    files = configure_runtime_logging(paths.log_dir)
    logger = logging.getLogger("native-lifecycle-redaction")
    logger.warning("startup token=visible csrf-key: visible password visible " + "x" * 120)
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = files["application"].read_text(encoding="utf-8")
    assert "visible" not in content
    assert "[REDACTED]" in content
    assert files["application"].with_suffix(".log.1").is_file()
    if os.name == "posix":
        assert files["application"].stat().st_mode & 0o077 == 0


def test_worker_redirected_streams_use_their_rotating_redacted_sink(monkeypatch, tmp_path):
    monkeypatch.setattr("scanner.runtime.runtime_logging._LOG_BYTES", 100)
    original_stdout, original_stderr = sys.stdout, sys.stderr
    try:
        redirect_standard_streams(tmp_path, "worker")
        print("EDITARR_INTERNAL_CONTROLLER_TOKEN=visible " + "x" * 150)
        print("Authorization: Bearer visible", file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr

    current = (tmp_path / "worker.log").read_text(encoding="utf-8")
    rotated = (tmp_path / "worker.log.1").read_text(encoding="utf-8")
    assert "visible" not in current + rotated
    assert "[REDACTED]" in current + rotated


def test_actual_macos_source_controller_waits_for_readiness_then_honors_second_launch(
    monkeypatch, tmp_path
):
    """Exercise a real local controller with only an injected browser opener."""

    paths = _paths(tmp_path)
    home = tmp_path / "home"
    for directory in (paths.data_dir, paths.log_dir, paths.temp_dir, home):
        directory.mkdir(parents=True)
    port = _unused_port()
    monkeypatch.setattr("scanner.runtime.runtime_paths._active_runtime_paths", paths)
    monkeypatch.setattr(cli.settings, "data_dir", paths.data_dir)
    monkeypatch.setattr(cli.settings, "log_dir", paths.log_dir)
    monkeypatch.setenv("EDITARR_DATA_DIR", str(paths.data_dir))
    monkeypatch.setenv("EDITARR_NATIVE_LAUNCHER", "1")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("TMPDIR", str(paths.temp_dir))
    opened: list[str] = []
    adapter = NativeLifecycleAdapter(browser_opener=lambda url: opened.append(url) or True)
    owner = NativeInstance(paths, adapter)
    parser = cli._build_parser()
    context = cli.resolve_server_launch_context(
        parser.parse_args(["--api-only", "--port", str(port)]), configuration=cli.settings,
        environment={}, original_argv=("--api-only", "--port", str(port)),
    )
    assert owner.claim_or_open_existing(context.snapshot.base_url + "/", open_ui=True)
    ready = Event()
    errors: list[BaseException] = []

    def controller() -> None:
        try:
            restart_service.run_controller(
                context,
                stop_event=owner.quit_event,
                on_ready=lambda snapshot: (
                    owner.ready(snapshot.base_url + "/", open_ui=True), ready.set()
                ),
            )
        except BaseException as error:  # pragma: no cover - assertion below reports it
            errors.append(error)

    thread = Thread(target=controller, daemon=True)
    thread.start()
    assert ready.wait(15), errors
    expected = f"http://localhost:{port}/"
    assert opened == [expected]

    second = NativeInstance(paths, adapter)
    assert not second.claim_or_open_existing("http://wrong.invalid/", open_ui=True)
    assert opened == [expected, expected]
    assert second.quit()
    thread.join(15)
    owner.close()
    assert not thread.is_alive()
    assert not errors


def test_browser_and_log_open_failures_are_diagnostic_only(tmp_path):
    adapter = NativeLifecycleAdapter(
        browser_opener=lambda _url: (_ for _ in ()).throw(OSError("browser unavailable")),
        log_opener=lambda _path: (_ for _ in ()).throw(OSError("folder unavailable")),
    )
    assert not adapter.open_ui("http://localhost:8000/")
    assert not adapter.open_logs(tmp_path)
