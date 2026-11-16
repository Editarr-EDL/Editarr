import sys
from threading import Event

import pytest

from scanner import cli
from scanner.runtime import restart_service
from scanner.runtime.native_lifecycle import NativeInstance
from scanner.runtime.runtime_paths import RuntimePaths
from scanner.settings.config import Settings
from scanner.settings.deployment_config import DeploymentStore
from tests.cli_support import cli_database as cli_database


def test_server_launch_passes_original_policy_to_controller(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(restart_service, "run_controller", captured.append)
    monkeypatch.setattr(cli.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cli.settings, "log_dir", tmp_path / "logs")
    monkeypatch.setenv("EDITARR_BASE_URL", "https://editarr.example")
    arguments = ["--api-only", "--host", "0.0.0.0", "--port", "9000"]
    monkeypatch.setattr(sys, "argv", ["editarr", *arguments])
    cli.main()
    context = captured[0]
    assert context.host == "0.0.0.0"
    assert context.cli_port_override == 9000
    assert context.api_only
    assert context.original_argv == tuple(arguments)


def test_native_launcher_uses_ready_snapshot_url_and_no_browser_seam(monkeypatch, tmp_path):
    calls: list[object] = []

    class Instance:
        def __init__(self, paths):
            calls.append(("instance", paths))
            self.quit_event = Event()

        def claim_or_open_existing(self, url, *, open_ui):
            calls.append(("claim", url, open_ui))
            return True

        def activate_existing(self, *, open_ui):
            calls.append(("activate", open_ui))
            return False

        def ready(self, url, *, open_ui):
            calls.append(("ready", url, open_ui))

        def close(self):
            calls.append("close")

    def controller(context, *, stop_event, on_ready):
        assert isinstance(stop_event, Event)
        on_ready(context.snapshot)

    monkeypatch.setattr(cli, "is_frozen_application", lambda: True)
    monkeypatch.setattr("scanner.runtime.native_lifecycle.NativeInstance", Instance)
    monkeypatch.setattr(restart_service, "run_controller", controller)
    monkeypatch.setattr(
        "scanner.runtime.runtime_logging.redirect_standard_streams", lambda *_: None
    )
    monkeypatch.setattr(cli.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cli.settings, "log_dir", tmp_path / "logs")
    monkeypatch.setenv("EDITARR_BASE_URL", "https://editarr.example/editarr")

    cli._run_server(cli._build_parser().parse_args(["--no-browser"]))

    assert calls[1:] == [
        ("activate", False),
        ("claim", "https://editarr.example/editarr/", False),
        ("ready", "https://editarr.example/editarr/", False),
        "close",
    ]


def test_linux_headless_native_launch_suppresses_browser_and_reports_url(
    monkeypatch, capsys, tmp_path
):
    calls: list[object] = []

    class Instance:
        def __init__(self, _paths):
            self.quit_event = Event()

        def activate_existing(self, *, open_ui):
            calls.append(("activate", open_ui))
            return False

        def claim_or_open_existing(self, url, *, open_ui):
            calls.append(("claim", url, open_ui))
            return True

        def ready(self, url, *, open_ui):
            calls.append(("ready", url, open_ui))

        def close(self):
            calls.append("close")

    def controller(context, *, stop_event, on_ready):
        assert isinstance(stop_event, Event)
        on_ready(context.snapshot)

    monkeypatch.setattr(cli, "is_frozen_application", lambda: True)
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr("scanner.runtime.native_lifecycle.NativeInstance", Instance)
    monkeypatch.setattr(restart_service, "run_controller", controller)
    monkeypatch.setattr(
        "scanner.runtime.runtime_logging.redirect_standard_streams", lambda *_: None
    )
    monkeypatch.setattr(cli.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cli.settings, "log_dir", tmp_path / "logs")
    monkeypatch.setenv("EDITARR_BASE_URL", "https://editarr.example")

    cli._run_server(cli._build_parser().parse_args([]))

    assert calls == [
        ("activate", False),
        ("claim", "https://editarr.example/", False),
        ("ready", "https://editarr.example/", False),
        "close",
    ]
    assert capsys.readouterr().out == "Editarr is ready at https://editarr.example/\n"


def test_linux_graphical_session_keeps_browser_activation():
    assert cli._desktop_browser_available(
        platform_name="linux", environment={"WAYLAND_DISPLAY": "wayland-0"}
    )
    assert cli._desktop_browser_available(
        platform_name="linux", environment={"DISPLAY": ":99"}
    )
    assert not cli._desktop_browser_available(platform_name="linux", environment={})
    assert cli._desktop_browser_available(platform_name="darwin", environment={})


def test_native_second_launch_exits_without_starting_controller(monkeypatch, tmp_path):
    # This case verifies graphical relaunch independently of the CI host session.
    monkeypatch.setenv("DISPLAY", ":99")
    calls: list[object] = []

    class Instance:
        def __init__(self, _paths):
            self.quit_event = Event()

        def activate_existing(self, *, open_ui):
            calls.append(("activate", open_ui))
            return True

    monkeypatch.setattr(cli, "is_frozen_application", lambda: True)
    monkeypatch.setattr("scanner.runtime.native_lifecycle.NativeInstance", Instance)
    monkeypatch.setattr(restart_service, "run_controller", lambda *_: calls.append("controller"))
    monkeypatch.setattr(
        cli,
        "resolve_server_launch_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("second launch must use the active owner's URL")
        ),
    )
    monkeypatch.setattr(
        "scanner.runtime.runtime_logging.redirect_standard_streams", lambda *_: None
    )
    monkeypatch.setattr(cli.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cli.settings, "log_dir", tmp_path / "logs")

    cli._run_server(cli._build_parser().parse_args([]))

    assert calls == [("activate", True)]


@pytest.mark.parametrize(
    ("action", "expected", "exit_code"),
    [
        ("status", '{"instance": {"pid": 42}, "running": true}', 0),
        ("open-ui", "", 0),
        ("open-logs", "", 0),
        ("quit", "", 0),
    ],
)
def test_lifecycle_command_uses_only_authenticated_native_adapter(
    monkeypatch, capsys, tmp_path, action, expected, exit_code
):
    calls: list[object] = []
    paths = RuntimePaths(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        log_dir=tmp_path / "logs",
        cache_dir=tmp_path / "cache",
        temp_dir=tmp_path / "tmp",
        resources_dir=tmp_path / "resources",
    )

    class Instance:
        def __init__(self, received_paths):
            assert received_paths == paths

        def status(self):
            calls.append("status")
            return {"pid": 42}

        def open_ui(self):
            calls.append("open-ui")
            return True

        def open_logs(self):
            calls.append("open-logs")
            return True

        def quit(self):
            calls.append("quit")
            return True

    monkeypatch.setattr(cli, "get_runtime_paths", lambda: paths)
    monkeypatch.setattr("scanner.runtime.native_lifecycle.NativeInstance", Instance)

    assert cli._run_lifecycle(cli._build_parser().parse_args(["lifecycle", action])) == exit_code
    assert calls == [action]
    assert capsys.readouterr().out.strip() == expected


def test_native_bind_failure_releases_singleton_state(monkeypatch, tmp_path):
    calls: list[object] = []

    class Instance:
        def __init__(self, _paths):
            self.quit_event = Event()

        def claim_or_open_existing(self, *_args, **_kwargs):
            return True

        def activate_existing(self, *, open_ui):
            return False

        def close(self):
            calls.append("close")

    def fail_controller(*_args, **_kwargs):
        raise RuntimeError("listener is occupied")

    monkeypatch.setattr(cli, "is_frozen_application", lambda: True)
    monkeypatch.setattr("scanner.runtime.native_lifecycle.NativeInstance", Instance)
    monkeypatch.setattr(restart_service, "run_controller", fail_controller)
    monkeypatch.setattr(
        "scanner.runtime.runtime_logging.redirect_standard_streams", lambda *_: None
    )
    monkeypatch.setattr(cli.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cli.settings, "log_dir", tmp_path / "logs")

    with pytest.raises(cli._CommandError, match="listener is occupied"):
        cli._run_server(cli._build_parser().parse_args([]))
    assert calls == ["close"]


def test_native_readiness_timeout_releases_the_actual_singleton(monkeypatch, tmp_path):
    paths = RuntimePaths(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        log_dir=tmp_path / "logs",
        cache_dir=tmp_path / "cache",
        temp_dir=tmp_path / "tmp",
        resources_dir=tmp_path / "resources",
    )

    def readiness_timeout(*_args, **_kwargs):
        raise RuntimeError(
            "HTTP readiness timed out; inspect the preceding diagnostic and start Editarr again"
        )

    monkeypatch.setattr(cli, "is_frozen_application", lambda: True)
    monkeypatch.setattr(cli, "get_runtime_paths", lambda: paths)
    monkeypatch.setattr(restart_service, "run_controller", readiness_timeout)
    monkeypatch.setattr(
        "scanner.runtime.runtime_logging.redirect_standard_streams", lambda *_: None
    )
    monkeypatch.setattr(cli.settings, "data_dir", paths.data_dir)
    monkeypatch.setattr(cli.settings, "log_dir", paths.log_dir)

    with pytest.raises(cli._CommandError, match="HTTP readiness timed out"):
        cli._run_server(cli._build_parser().parse_args(["--no-browser"]))

    assert not (paths.data_dir / "native-instance" / "instance.json").exists()
    replacement = NativeInstance(paths)
    assert replacement.claim_or_open_existing("http://localhost:8123/", open_ui=False)
    replacement.close()


def test_server_launch_context_uses_saved_port_then_default_and_keeps_cli_override(
    monkeypatch, tmp_path
):
    configuration = Settings(data_dir=tmp_path)
    parser = cli._build_parser()

    default = cli.resolve_server_launch_context(
        parser.parse_args([]), configuration=configuration, environment={}
    )
    assert default.snapshot.listening_port == 3348
    assert default.snapshot.port_source == "default"
    assert default.snapshot.base_url == "http://localhost:3348"
    assert default.snapshot.cookie_secure is False

    DeploymentStore(tmp_path).update(port=9123)
    # A settings save changes only the next resolver result, never the active
    # snapshot carried by an already-running HTTP generation.
    assert default.snapshot.listening_port == 3348
    assert default.snapshot.base_url == "http://localhost:3348"
    saved = cli.resolve_server_launch_context(
        parser.parse_args([]), configuration=configuration, environment={}
    )
    assert saved.snapshot.listening_port == 9123
    assert saved.snapshot.port_source == "saved"
    assert saved.snapshot.base_url == "http://localhost:9123"

    overridden = cli.resolve_server_launch_context(
        parser.parse_args(["--port", "9234"]), configuration=configuration, environment={}
    )
    assert overridden.snapshot.listening_port == 9234
    assert overridden.snapshot.port_source == "cli"
    assert overridden.snapshot.cli_port_override == 9234
    assert overridden.cli_port_override == 9234

