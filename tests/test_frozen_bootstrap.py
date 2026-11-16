"""Frozen/source executable bootstrap and hidden-role coverage."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scanner import entry
from scanner.runtime.runtime_paths import resolve_resource_directory, resolve_runtime_paths


def test_scan_worker_command_keeps_source_development_module_invocation():
    assert entry.scan_worker_command(
        "1f9b0e6e-5a03-4ea8-b42a-f1b723ad84af",
        "opaque-token",
        executable="/venv/bin/python",
        frozen=False,
    ) == [
        "/venv/bin/python",
        "-m",
        "scanner.worker",
        "--scan-id",
        "1f9b0e6e-5a03-4ea8-b42a-f1b723ad84af",
        "--worker-token",
        "opaque-token",
    ]


def test_scan_worker_command_relaunches_the_frozen_program_through_private_role():
    assert entry.scan_worker_command(
        "1f9b0e6e-5a03-4ea8-b42a-f1b723ad84af",
        "opaque-token",
        executable="/Applications/Editarr.app/Contents/MacOS/Editarr",
        frozen=True,
    ) == [
        "/Applications/Editarr.app/Contents/MacOS/Editarr",
        "--internal-role",
        "scan-worker",
        "--scan-id",
        "1f9b0e6e-5a03-4ea8-b42a-f1b723ad84af",
        "--worker-token",
        "opaque-token",
    ]


def test_valid_private_scan_role_invokes_the_worker_once_without_public_parser():
    calls: list[tuple[str, str]] = []
    scan_id = "1f9b0e6e-5a03-4ea8-b42a-f1b723ad84af"

    result = entry.dispatch_internal_role(
        [
            "--internal-role",
            "scan-worker",
            "--scan-id",
            scan_id,
            "--worker-token",
            "opaque-token",
        ],
        scan_runner=lambda received_id, token: calls.append((received_id, token)) or 7,
    )

    assert result == 7
    assert calls == [(scan_id, "opaque-token")]


def test_private_roles_are_absent_from_public_cli_help():
    from scanner.cli import _build_parser

    help_text = _build_parser().format_help()

    assert "--internal-role" not in help_text
    assert "scan-worker" not in help_text
    assert "http-child" not in help_text


@pytest.mark.parametrize(
    "argv",
    [
        ["--internal-role"],
        ["--internal-role", "unknown-role"],
        ["--internal-role", "scan-worker", "--scan-id", "not-a-uuid", "--worker-token", "x"],
        ["--internal-role", "scan-worker", "--scan-id", "1f9b0e6e-5a03-4ea8-b42a-f1b723ad84af"],
        ["--internal-role", "http-child", "--scan-id", "1f9b0e6e-5a03-4ea8-b42a-f1b723ad84af"],
    ],
)
def test_malformed_private_roles_are_rejected_before_a_worker_import(argv):
    with pytest.raises(entry.InternalRoleError):
        entry.dispatch_internal_role(argv)


def test_entry_calls_freeze_support_before_path_bootstrap_and_role_dispatch(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(entry.multiprocessing, "freeze_support", lambda: events.append("freeze"))
    monkeypatch.setattr(
        entry,
        "bootstrap_entrypoint_runtime_paths",
        lambda _argv: events.append("paths"),
    )
    monkeypatch.setattr(
        entry,
        "dispatch_internal_role",
        lambda _argv: events.append("role") or 0,
    )

    entry.main(["--internal-role", "http-child"])

    assert events == ["freeze", "paths", "role"]


def test_malformed_private_invocation_does_not_import_settings_or_echo_token(tmp_path):
    token = "must-not-appear-in-output"
    source_root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    runtime_temp = tmp_path / "runtime-temp"
    environment = os.environ | {
        "HOME": str(home),
        "TMPDIR": str(runtime_temp),
        "PYTHONPATH": str(source_root),
    }
    program = """
import sys
from scanner import entry
assert 'scanner.settings.config' not in sys.modules
try:
    entry.main([
        '--internal-role', 'scan-worker', '--scan-id', 'not-a-uuid',
        '--worker-token', 'must-not-appear-in-output',
    ])
except SystemExit as error:
    assert error.code == 2
else:
    raise AssertionError('expected malformed private role to fail')
assert 'scanner.settings.config' not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert token not in result.stdout + result.stderr
    assert "invalid internal Editarr invocation" in result.stderr
    assert not home.exists()


def test_resource_resolution_uses_frozen_bundle_not_runtime_roots(tmp_path):
    bundle = tmp_path / "Editarr bundle" / "resources"
    executable = tmp_path / "Editarr bundle" / "Editarr"
    paths = resolve_runtime_paths(
        frozen=True,
        executable=executable,
        bundle_directory=bundle,
        environment={},
        home_dir=tmp_path / "home",
        temporary_dir=tmp_path / "temp",
    )

    assert resolve_resource_directory(
        frozen=True, executable=executable, bundle_directory=bundle
    ) == bundle
    assert paths.resources_dir == bundle
    assert paths.resources_dir not in {
        paths.data_dir,
        paths.cache_dir,
        paths.log_dir,
        paths.temp_dir,
    }
    assert not bundle.exists()
