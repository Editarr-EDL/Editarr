"""Regression coverage for the verifier's Windows CIM process inventory."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts import verifier_windows_processes


def test_windows_process_inventory_retries_timeout_and_preserves_full_command_line(monkeypatch):
    command_line = r'"C:\Runtime ✓\Editarr.exe" --internal-role http-child'
    records = [
        {"ProcessId": 17, "ParentProcessId": 5, "CommandLine": command_line},
        {"ProcessId": 18, "ParentProcessId": 5, "CommandLine": None},
    ]
    timeouts = []

    def run(command, *, capture_output, encoding, check, timeout):
        assert command[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
        assert "-Property ProcessId,ParentProcessId,CommandLine" in command[4]
        assert capture_output and encoding == "utf-8" and check
        timeouts.append(timeout)
        if len(timeouts) == 1:
            raise subprocess.TimeoutExpired(command, timeout, output=b"partial JSON")
        return SimpleNamespace(stdout="\ufeff" + json.dumps(records, ensure_ascii=False))

    monkeypatch.setattr(verifier_windows_processes.subprocess, "run", run)

    assert verifier_windows_processes.windows_process_rows() == [(17, 5, command_line)]
    assert timeouts == [15, 30]


def test_windows_process_inventory_fails_if_both_queries_time_out(monkeypatch):
    timeouts = []

    def run(command, *, timeout, **_options):
        timeouts.append(timeout)
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(verifier_windows_processes.subprocess, "run", run)

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        verifier_windows_processes.windows_process_rows()

    assert raised.value.timeout == 30
    assert timeouts == [15, 30]
