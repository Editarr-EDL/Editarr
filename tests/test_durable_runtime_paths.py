"""Focused durable-state consumers for the native runtime-path contract."""

from __future__ import annotations

import stat
from pathlib import Path

from scanner import cli
from scanner.runtime.runtime_logging import configure_runtime_logging
from scanner.runtime.runtime_paths import RuntimePaths
from scanner.security.auth_audit import AuditContext, AuthenticationAuditLogger, new_request_id
from scanner.settings.config import Settings
from scanner.settings.deployment_config import DeploymentStore


def _paths(tmp_path: Path) -> RuntimePaths:
    return RuntimePaths(
        data_dir=tmp_path / "durable data",
        config_dir=tmp_path / "config",
        log_dir=tmp_path / "diagnostic logs",
        cache_dir=tmp_path / "cache",
        temp_dir=tmp_path / "temporary",
        resources_dir=tmp_path / "resources",
    )


def test_injected_runtime_paths_drive_durable_defaults_and_database_override(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    monkeypatch.setattr("scanner.runtime.runtime_paths._active_runtime_paths", paths)

    derived = Settings()
    assert derived.data_dir == paths.data_dir
    assert derived.database_url == f"sqlite:///{paths.data_dir / 'scanner.db'}"
    assert derived.exports_dir == paths.data_dir / "exports"
    assert derived.qwen_replays_dir == paths.data_dir / "qwen-replays"
    assert derived.log_dir == paths.log_dir
    assert derived.cache_dir == paths.cache_dir
    assert derived.model_cache == paths.model_cache
    assert derived.frames_dir == paths.temp_dir / "editarr-scans"

    explicit_database = "sqlite:////portable/diagnostic.sqlite"
    monkeypatch.setenv("EDITARR_DATABASE_URL", explicit_database)
    monkeypatch.setenv("EDITARR_EXPORTS_DIR", str(tmp_path / "operator exports"))
    monkeypatch.setenv("EDITARR_QWEN_REPLAYS_DIR", str(tmp_path / "operator replays"))
    overridden = Settings()
    assert overridden.database_url == explicit_database
    assert overridden.data_dir == paths.data_dir
    assert overridden.exports_dir == tmp_path / "operator exports"
    assert overridden.qwen_replays_dir == tmp_path / "operator replays"


def test_durable_deployment_and_auth_audit_use_separate_private_runtime_roots(
    monkeypatch, tmp_path
):
    paths = _paths(tmp_path)
    monkeypatch.setattr("scanner.runtime.runtime_paths._active_runtime_paths", paths)

    DeploymentStore(paths.data_dir).update(port=9123)
    audit = AuthenticationAuditLogger(paths.log_dir)
    audit.emit(
        event="auth.login",
        outcome="failure",
        severity="warning",
        context=AuditContext(source="system", request_id=new_request_id()),
        actor="anonymous",
        reason_code="invalid_credentials",
    )

    deployment = paths.data_dir / "deployment.json"
    audit_log = paths.log_dir / "auth-audit.jsonl"
    assert deployment.is_file()
    assert audit_log.is_file()
    assert not (paths.data_dir / "logs").exists()
    assert stat.S_IMODE(paths.data_dir.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(deployment.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(paths.log_dir.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(audit_log.stat().st_mode) & 0o077 == 0


def test_runtime_log_policy_uses_private_rotating_process_role_files(
    monkeypatch, tmp_path
):
    paths = _paths(tmp_path)
    monkeypatch.setattr("scanner.runtime.runtime_paths._active_runtime_paths", paths)

    log_files = configure_runtime_logging(paths.log_dir)

    assert log_files == {
        "application": paths.log_dir / "application.log",
        "controller": paths.log_dir / "controller.log",
        "http": paths.log_dir / "http.log",
        "worker": paths.log_dir / "worker.log",
    }
    assert all(path.is_file() for path in log_files.values())
    assert all(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0 for path in log_files.values())


def test_paths_command_is_offline_non_secret_and_does_not_create_runtime_directories(
    monkeypatch, tmp_path, capsys
):
    paths = _paths(tmp_path)
    monkeypatch.setattr("scanner.runtime.runtime_paths._active_runtime_paths", paths)

    assert cli._run_paths() == 0

    assert capsys.readouterr().out.splitlines() == [
        f"data: {paths.data_dir}",
        f"config: {paths.config_dir}",
        f"logs: {paths.log_dir}",
        f"cache: {paths.cache_dir}",
        f"temp: {paths.temp_dir}",
        f"resources: {paths.resources_dir}",
    ]
    assert not any(
        path.exists()
        for path in (
            paths.data_dir,
            paths.config_dir,
            paths.log_dir,
            paths.cache_dir,
            paths.temp_dir,
            paths.resources_dir,
        )
    )
