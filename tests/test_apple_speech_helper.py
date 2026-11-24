"""Test source and wheel helper installation without a macOS SDK."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scanner.pipeline.processes import ScanCancelledError
from scanner.runtime.apple_speech import helper


@pytest.fixture
def source_runtime(tmp_path, monkeypatch):
    """Make an installed source package and a separate writable helper cache."""
    installed_package = tmp_path / "site-packages" / "scanner"
    source = installed_package / "runtime" / "apple_speech" / "AppleSpeech.swift"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"first Swift source")
    probed = []

    def probe(command, *, stderr, text, timeout):
        assert command == ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version"]
        assert stderr == subprocess.DEVNULL and text and timeout > 0
        probed.append(command)
        return "26.0\n"

    monkeypatch.setattr(helper, "SOURCE", source)
    monkeypatch.setattr(helper, "is_frozen_application", lambda: False)
    monkeypatch.setattr(helper.subprocess, "check_output", probe)
    helper.runtime_available.cache_clear()
    try:
        yield source, tmp_path / "cache", probed
    finally:
        helper.runtime_available.cache_clear()


def _write_compiler_outputs(command, source, cache):
    assert command[:3] == [sys.executable, str(helper._COMPILER_WATCHDOG), str(os.getpid())]
    assert command[3:7] == ["/usr/bin/xcrun", "--sdk", "macosx", "swiftc"]
    assert str(source) in command
    assert "-target" in command
    output = Path(command[command.index("-o") + 1])
    modules = Path(command[command.index("-module-cache-path") + 1])
    assert modules.parent == output.parent
    assert output.parent.parent == cache / "helpers" / helper.HELPER_NAME
    modules.mkdir()
    (modules / "swiftmodule").write_bytes(b"temporary compiler module")
    output.write_bytes(source.read_bytes())
    output.chmod(0o755)
    return output


def test_resolve_helper_builds_once_and_rebuilds_after_swift_source_changes(
    source_runtime, monkeypatch
):
    source, cache, probed = source_runtime
    builds = []

    def compile_command(command, *, should_cancel):
        assert should_cancel is None
        builds.append(_write_compiler_outputs(command, source, cache))

    monkeypatch.setattr(helper, "run_command", compile_command)

    assert helper.runtime_available()
    assert not cache.exists()
    first = helper.resolve_helper(cache, should_cancel=None)
    assert first.read_bytes() == b"first Swift source"
    assert os.access(first, os.X_OK)
    assert helper.resolve_helper(cache, should_cancel=None) == first
    assert len(builds) == 1

    source.write_bytes(b"updated Swift source")
    second = helper.resolve_helper(cache, should_cancel=None)
    assert second != first
    assert second.read_bytes() == b"updated Swift source"
    assert first.read_bytes() == b"first Swift source"
    assert os.access(second, os.X_OK)
    assert len(builds) == 2
    assert len(probed) == 1
    assert all(not build.parent.exists() for build in builds)
    assert set(second.parent.iterdir()) == {first, second}


@pytest.mark.parametrize(
    "sdk_version",
    [pytest.param("15.5\n", id="older-sdk"), pytest.param(None, id="missing-sdk")],
)
def test_resolve_helper_rejects_unusable_sdk_without_starting_build(
    source_runtime, monkeypatch, sdk_version
):
    _source, cache, _probed = source_runtime

    def probe(command, **_options):
        if sdk_version is None:
            raise subprocess.CalledProcessError(1, command)
        return sdk_version

    def compile_command(*_args, **_options):
        pytest.fail("an unsupported or missing SDK must not start the compiler")

    monkeypatch.setattr(helper.subprocess, "check_output", probe)
    monkeypatch.setattr(helper, "run_command", compile_command)

    assert not helper.runtime_available()
    with pytest.raises(RuntimeError, match="Command Line Tools with a macOS 26 SDK"):
        helper.resolve_helper(cache, should_cancel=None)
    assert not cache.exists()


@pytest.mark.parametrize(
    ("cancelled", "error_type", "error_message"),
    [
        (False, RuntimeError, "Could not build the Apple Speech helper: swiftc failed"),
        (True, ScanCancelledError, "Scan cancelled"),
    ],
)
def test_resolve_helper_cleans_partial_outputs_on_failed_or_cancelled_compile(
    source_runtime, monkeypatch, cancelled, error_type, error_message
):
    source, cache, _probed = source_runtime

    def should_cancel():
        return cancelled

    def interrupted_compile(command, *, should_cancel):
        _write_compiler_outputs(command, source, cache)
        if should_cancel():
            raise ScanCancelledError("Scan cancelled")
        raise subprocess.CalledProcessError(1, command, stderr="swiftc failed")

    monkeypatch.setattr(helper, "run_command", interrupted_compile)

    with pytest.raises(error_type, match=error_message):
        helper.resolve_helper(cache, should_cancel=should_cancel)

    assert list((cache / "helpers" / helper.HELPER_NAME).iterdir()) == []

    monkeypatch.setattr(
        helper, "run_command", lambda command, **_: _write_compiler_outputs(command, source, cache)
    )
    rebuilt = helper.resolve_helper(cache, should_cancel=None)
    assert os.access(rebuilt, os.X_OK)
    assert list(rebuilt.parent.iterdir()) == [rebuilt]
