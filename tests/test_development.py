"""Focused development-topology and launcher checks for RP-11."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scanner.cli import _build_parser, resolve_server_launch_context
from scanner.runtime.development import resolve_development_topology
from scanner.settings.config import Settings
from scanner.settings.deployment_config import DeploymentConfigurationError, DeploymentStore

ROOT = Path(__file__).resolve().parents[1]


def _launcher_module():
    path = ROOT / "scripts" / "run_development.py"
    spec = importlib.util.spec_from_file_location("editarr_run_development", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path)


def _isolated_runtime_environment(tmp_path: Path) -> dict[str, str]:
    """Keep subprocess-derived native runtime roots below the pytest temporary tree."""

    home = tmp_path / "home"
    runtime_temp = tmp_path / "runtime"
    home.mkdir()
    runtime_temp.mkdir()
    return {"HOME": str(home), "TMPDIR": str(runtime_temp)}


def test_local_development_uses_vite_as_temporary_browser_base_without_persistence(tmp_path):
    topology = resolve_development_topology(
        configuration=_settings(tmp_path),
        environment={},
        api_port=18101,
        web_port=18102,
    )

    assert topology.browser_url == "http://localhost:18102/"
    assert topology.api_target == "http://127.0.0.1:18101"
    assert topology.api_path == "/api"
    assert topology.snapshot.base_url == "http://localhost:18102"
    assert topology.snapshot.base_url_source == "local_default"
    assert topology.snapshot.cookie_secure is False
    assert not (tmp_path / "deployment.json").exists()
    child = topology.child_environment({})
    assert "EDITARR_BASE_URL" not in child
    assert child["EDITARR_AUTH_COOKIE_SECURE"] == "false"
    assert child["EDITARR_VITE_BASE_PATH"] == "/"


def test_wildcard_development_keeps_a_usable_browser_origin(tmp_path):
    topology = resolve_development_topology(
        configuration=_settings(tmp_path), environment={}, web_host="0.0.0.0"
    )
    assert topology.web_host == "0.0.0.0"
    assert topology.browser_origin == "http://localhost:5173"
    assert topology.snapshot.base_url_source == "local_default"
    assert topology.child_environment({})["EDITARR_DEVELOPMENT_WEB_HOST"] == "0.0.0.0"


def test_explicit_empty_environment_does_not_leak_process_base_url(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITARR_BASE_URL", "https://unrelated.example/other")
    topology = resolve_development_topology(
        configuration=_settings(tmp_path),
        environment={},
        api_port=18103,
        web_port=18104,
    )
    assert topology.snapshot.base_url == "http://localhost:18104"
    assert topology.snapshot.base_url_source == "local_default"


def test_saved_base_url_and_port_win_when_they_match_prefixed_vite(tmp_path):
    store = DeploymentStore(tmp_path)
    store.update(base_url="http://localhost:18112/media/editarr", port=18111)
    before = (tmp_path / "deployment.json").read_bytes()

    topology = resolve_development_topology(
        configuration=_settings(tmp_path), environment={}, web_port=18112
    )

    assert topology.browser_url == "http://localhost:18112/media/editarr/"
    assert topology.api_path == "/media/editarr/api"
    assert topology.snapshot.listening_port == 18111
    assert topology.snapshot.base_url_source == "saved"
    assert topology.snapshot.port_source == "saved"
    assert (tmp_path / "deployment.json").read_bytes() == before


def test_saved_base_url_wins_over_environment_seed(tmp_path):
    DeploymentStore(tmp_path).update(base_url="http://localhost:18122/saved")
    topology = resolve_development_topology(
        configuration=_settings(tmp_path),
        environment={"EDITARR_BASE_URL": "http://localhost:18122/environment"},
        api_port=18121,
        web_port=18122,
    )
    assert topology.snapshot.base_url == "http://localhost:18122/saved"
    assert topology.snapshot.prefix == "/saved"
    assert topology.snapshot.base_url_source == "saved"


@pytest.mark.parametrize("web_host", ["localhost", "127.0.0.1", "0.0.0.0"])
@pytest.mark.parametrize("prefix", ["/", "/media/editarr"])
@pytest.mark.parametrize("port_source", ["default", "saved", "cli"])
def test_reload_factory_preserves_browser_host_and_exact_source_policy(
    tmp_path, web_host, prefix, port_source
):
    environment = {
        "PATH": str(Path(sys.executable).parent),
        "PYTHONPATH": str(ROOT),
        "EDITARR_DATA_DIR": str(tmp_path),
        "EDITARR_DATABASE_URL": f"sqlite:///{tmp_path / 'scanner.db'}",
        **_isolated_runtime_environment(tmp_path),
    }
    if prefix != "/":
        browser_host = "localhost" if web_host == "0.0.0.0" else web_host
        DeploymentStore(tmp_path).update(base_url=f"http://{browser_host}:18124{prefix}")
        environment["EDITARR_BASE_URL"] = "https://ignored-seed.example/other"
    if port_source != "default":
        DeploymentStore(tmp_path).update(port=18122)
    topology = resolve_development_topology(
        configuration=_settings(tmp_path),
        environment=environment,
        api_port=18123 if port_source == "cli" else None,
        web_host=web_host,
        web_port=18124,
    )
    # Cross the same environment/process boundary as the Uvicorn reload worker.
    # Exercise the real app factory and auth middleware, not just the export value.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""\
                import os
                import sys
                from pathlib import Path
                from fastapi.testclient import TestClient
                from scanner.runtime.development import create_development_app
                from scanner.db import get_session_factory, init_db
                from scanner.db import UserProviderIdentityRow, UserRow
                from scanner.security.auth_repository import issue_session
                from scanner.settings.config import settings
                from scanner.settings.deployment_config import DeploymentStore

                browser_origin, prefix, source, port_source, port = sys.argv[1:]
                deployment_path = Path(settings.data_dir) / "deployment.json"
                before = deployment_path.read_bytes() if deployment_path.exists() else None
                app = create_development_app()
                snapshot = app.state.deployment_snapshot
                assert snapshot.public_origin.serialized == browser_origin
                assert snapshot.base_url_source == source
                assert snapshot.port_source == port_source
                assert snapshot.listening_port == int(port)
                expected_override = int(port) if port_source == "cli" else None
                assert snapshot.cli_port_override == expected_override
                assert app.state.launch_context.cli_port_override == expected_override
                assert snapshot.prefix == prefix
                assert snapshot.cookie_secure is False
                assert app.state.frontend_assets is None
                assert app.state.restart_capability.reason == "development_reload"
                init_db()
                client = TestClient(app, base_url=browser_origin)
                api_path = "/api" if prefix == "/" else prefix + "/api"
                assert client.get(api_path + "/auth/status").status_code == 200
                if prefix != "/":
                    assert client.get("/api/auth/status").status_code == 404
                payload = {"username": "Admin", "password": "correct horse battery staple"}
                other_host = "localhost" if "127.0.0.1" in browser_origin else "127.0.0.1"
                rejected = client.post(api_path + "/auth/jellyfin/login", json=payload,
                    headers={"Origin": "http://" + other_host + ":18124"})
                assert rejected.status_code == 403, rejected.text
                assert rejected.json()["code"] == "origin_failed"
                # A matching origin reaches the real sign-in route's setup gate.
                accepted = client.post(api_path + "/auth/jellyfin/login", json=payload,
                    headers={"Origin": browser_origin})
                assert accepted.status_code == 423, accepted.text
                assert accepted.json()["code"] == "setup_required"
                if source == "local_default":
                    for host in ("10.0.0.64", "10.0.0.99"):
                        direct_origin = "http://" + host + ":18124"
                        direct = client.post(direct_origin + api_path + "/auth/jellyfin/login",
                            json=payload, headers={"Origin": direct_origin})
                        assert direct.status_code == 423, direct.text
                        cross_origin = client.post(
                            direct_origin + api_path + "/auth/jellyfin/login",
                            json=payload, headers={"Origin": browser_origin,
                            "X-Forwarded-Host": "localhost:18124"})
                        assert cross_origin.status_code == 403, cross_origin.text
                after = deployment_path.read_bytes() if deployment_path.exists() else None
                assert after == before

                # Seed only isolated auth records; no startup scheduler or worker runs.
                with get_session_factory()() as db:
                    db.add(UserRow(id=1, role="owner", status="active",
                        local_export_id="local-" + "1" * 32))
                    db.flush()
                    db.add(UserProviderIdentityRow(user_id=1, server_id="server",
                        provider_user_id="1" * 32, link_state="verified"))
                    db.commit()
                    session = issue_session(db, remember_me=False, configuration=settings)
                headers = {"Origin": browser_origin, "X-CSRF-Token": session.csrf_token,
                    "Cookie": settings.auth_cookie_name + "=" + session.token}
                settings_path = api_path + "/settings/deployment"
                initial = client.get(settings_path, headers=headers)
                assert initial.status_code == 200, initial.text
                initial = initial.json()
                assert initial["restart_required"] is False, initial
                assert initial["active"] == initial["next_start"]
                assert initial["active"]["port_source"] == port_source
                assert initial["restart"]["supported"] is False

                changed = client.put(settings_path, json={"port": 18125}, headers=headers)
                assert changed.status_code == 200, changed.text
                changed = changed.json()
                assert changed["active"] == initial["active"]
                assert changed["next_start"]["base_url"] == initial["active"]["base_url"]
                assert changed["next_start"]["listening_port"] == (
                    int(port) if port_source == "cli" else 18125)
                assert changed["next_start"]["port_source"] == (
                    "cli" if port_source == "cli" else "saved")
                assert changed["restart_required"] is (port_source != "cli")
                store = DeploymentStore(settings.data_dir)
                assert store.read().base_url == (None if prefix == "/" else browser_origin + prefix)
                assert store.read().port == 18125

                saved_before_invalid = deployment_path.read_bytes()
                invalid = client.put(settings_path,
                    json={"base_url": "https://incompatible.example/other"}, headers=headers)
                assert invalid.status_code == 422, invalid.text
                assert "./scripts/dev.sh api" in invalid.text
                assert deployment_path.read_bytes() == saved_before_invalid

                moved = client.put(settings_path,
                    json={"base_url": browser_origin + "/next"}, headers=headers)
                assert moved.status_code == 200, moved.text
                assert moved.json()["active"] == initial["active"]
                assert moved.json()["next_start"]["base_url"] == browser_origin + "/next"
                assert moved.json()["next_start"]["base_url_source"] == "saved"
                assert moved.json()["restart_required"] is True
                assert client.get("/next/api/auth/status", headers=headers).status_code == 404
                assert client.get(api_path + "/auth/status").status_code == 200
                os.environ["EDITARR_DEVELOPMENT_WEB_HOST"] = other_host
                os.environ["EDITARR_BASE_URL"] = "https://changed.example/other"
                assert app.state.deployment_snapshot is snapshot
                assert client.post(api_path + "/auth/logout", headers=headers).status_code == 200
                client.close()
                """),
            topology.browser_origin,
            prefix,
            topology.snapshot.base_url_source,
            port_source,
            str(topology.snapshot.listening_port),
        ],
        env=topology.child_environment(environment),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert DeploymentStore(tmp_path).read().base_url == topology.browser_origin + "/next"


def test_conflicting_saved_base_url_is_rejected_without_writing(tmp_path):
    store = DeploymentStore(tmp_path)
    store.update(base_url="https://editarr.example/media", port=18131)
    before = (tmp_path / "deployment.json").read_bytes()

    with pytest.raises(DeploymentConfigurationError, match="effective saved Base URL") as error:
        resolve_development_topology(
            configuration=_settings(tmp_path), environment={}, web_port=18132
        )

    assert "./scripts/dev.sh api" in str(error.value)
    assert (tmp_path / "deployment.json").read_bytes() == before


def test_reload_factory_rejects_a_newly_incompatible_saved_url_without_writing(tmp_path):
    environment = {
        "PATH": str(Path(sys.executable).parent),
        "PYTHONPATH": str(ROOT),
        "EDITARR_DATA_DIR": str(tmp_path),
        "EDITARR_DATABASE_URL": f"sqlite:///{tmp_path / 'scanner.db'}",
        **_isolated_runtime_environment(tmp_path),
    }
    topology = resolve_development_topology(
        configuration=_settings(tmp_path), environment=environment, web_host="127.0.0.1"
    )
    DeploymentStore(tmp_path).update(base_url="https://public.example/editarr")
    before = (tmp_path / "deployment.json").read_bytes()
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""\
            from scanner.runtime.development import create_development_app
            from scanner.settings.deployment_config import DeploymentConfigurationError
            try:
                create_development_app()
            except DeploymentConfigurationError as error:
                assert "effective saved Base URL" in str(error)
                assert "./scripts/dev.sh api" in str(error)
            else:
                raise AssertionError("Incompatible saved URL was accepted")
            """)],
        env=topology.child_environment(environment),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "deployment.json").read_bytes() == before


def test_requested_api_and_web_port_conflict_is_explicit(tmp_path):
    with pytest.raises(DeploymentConfigurationError, match="conflicts with Vite port"):
        resolve_development_topology(
            configuration=_settings(tmp_path),
            environment={},
            api_port=18141,
            web_port=18141,
        )


def test_saved_api_port_and_vite_port_conflict_is_explicit(tmp_path):
    DeploymentStore(tmp_path).update(port=18142)
    with pytest.raises(DeploymentConfigurationError, match="API port 18142 conflicts"):
        resolve_development_topology(
            configuration=_settings(tmp_path), environment={}, web_port=18142
        )


def test_development_preserves_explicit_false_and_refuses_explicit_true(tmp_path):
    false_topology = resolve_development_topology(
        configuration=_settings(tmp_path),
        environment={"EDITARR_AUTH_COOKIE_SECURE": "false"},
        api_port=18143,
        web_port=18144,
    )
    assert false_topology.child_environment(
        {"EDITARR_AUTH_COOKIE_SECURE": "false"}
    )["EDITARR_AUTH_COOKIE_SECURE"] == "false"

    with pytest.raises(DeploymentConfigurationError, match="requests secure cookies"):
        resolve_development_topology(
            configuration=_settings(tmp_path),
            environment={"EDITARR_AUTH_COOKIE_SECURE": "true"},
            api_port=18143,
            web_port=18144,
        )


def test_api_only_resolves_saved_settings_without_writing(tmp_path):
    store = DeploymentStore(tmp_path)
    store.update(base_url="https://editarr.example/media", port=18145)
    deployment_path = tmp_path / "deployment.json"
    before = deployment_path.read_bytes()
    context = resolve_server_launch_context(
        _build_parser().parse_args(["--api-only"]),
        configuration=_settings(tmp_path),
        environment={},
    )
    assert context.api_only
    assert context.snapshot.base_url == "https://editarr.example/media"
    assert context.snapshot.listening_port == 18145
    assert deployment_path.read_bytes() == before


def test_check_mode_reports_exact_topology_without_launching(tmp_path):
    environment = {
        "PATH": str(Path(sys.executable).parent),
        "EDITARR_DATA_DIR": str(tmp_path),
        "EDITARR_DATABASE_URL": f"sqlite:///{tmp_path / 'scanner.db'}",
        **_isolated_runtime_environment(tmp_path),
    }
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_development.py",
            "dev",
            "--api-port",
            "18151",
            "--web-port",
            "18152",
            "--check",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Web UI: http://localhost:18152/" in result.stdout
    assert "http://localhost:18152/api -> http://127.0.0.1:18151/api" in result.stdout
    assert not (tmp_path / "deployment.json").exists()


def test_reload_factory_bootstraps_checkout_data_without_cli_import(tmp_path):
    environment = {
        "PATH": str(Path(sys.executable).parent),
        "PYTHONPATH": str(ROOT),
        **_isolated_runtime_environment(tmp_path),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import scanner.runtime.development; "
            "from scanner.settings.config import settings; print(settings.data_dir)",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(ROOT / "data")


def test_reload_command_disables_uvicorn_proxy_rewriting():
    source = (ROOT / "scripts" / "run_development.py").read_text(encoding="utf-8")
    assert '"--reload"' in source
    assert '"--no-proxy-headers"' in source
    assert "EDITARR_AUTH_PUBLIC_ORIGIN" not in source


def test_production_vite_contract_keeps_relative_build_base():
    config = (ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")
    assert 'base: "./"' in config
    assert 'if (command !== "serve") return html' in config
    assert "allowedHosts: true" in config


def test_process_pair_cleans_up_surviving_process_after_failure():
    launcher = _launcher_module()

    class Process:
        def __init__(self, returncode):
            self.returncode = returncode
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

    failed = Process(7)
    survivor = Process(None)
    created = iter((failed, survivor))
    result = launcher._run_process_pair(
        ["api"],
        ["web"],
        {},
        popen=lambda *args, **kwargs: next(created),
        pause=lambda _: None,
    )
    assert result == 7
    assert survivor.terminated


@pytest.mark.parametrize("canonical_web_host", ["localhost", "127.0.0.1"])
def test_vite_binds_ipv4_loopback_for_canonical_host_redirects(canonical_web_host):
    launcher = _launcher_module()

    assert launcher._vite_bind_host(canonical_web_host) == "127.0.0.1"


def test_launcher_defaults_to_all_interfaces_without_loopback_redirects():
    launcher = _launcher_module()
    assert launcher._parser().parse_args(["dev"]).web_host == "0.0.0.0"
    assert launcher._vite_bind_host("0.0.0.0") == "0.0.0.0"


def test_process_pair_cleans_up_api_if_web_process_fails_to_spawn():
    launcher = _launcher_module()

    class Process:
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

    api = Process()
    calls = 0

    def popen(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Vite executable unavailable")
        return api

    with pytest.raises(OSError, match="Vite executable unavailable"):
        launcher._run_process_pair(["api"], ["web"], {}, popen=popen)
    assert api.terminated
