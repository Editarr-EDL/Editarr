"""Unit checks for the NATIVE-11 black-box verifier contract."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tomllib
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest


def _verifier():
    path = Path("scripts/verify_installed_release.py")
    spec = importlib.util.spec_from_file_location("installed_artifact_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _runtime_inventory(runtime: Path, **overrides):
    version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    payload = {
        "format": 1,
        "version": version,
        "target": "macos-arm64",
        "source_revision": "a" * 40,
        **overrides,
    }
    runtime.mkdir()
    (runtime / "editarr-runtime-inventory.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_runtime_identity_comes_from_packaged_inventory(tmp_path):
    verifier = _verifier()
    runtime = tmp_path / "Editarr"
    expected = _runtime_inventory(runtime)

    metadata = verifier._runtime_inventory(runtime, "macos-arm64")

    assert metadata["version"] == expected["version"]
    assert metadata["target"] == expected["target"]
    assert metadata["source_revision"] == expected["source_revision"]
    assert len(metadata["inventory_sha256"]) == 64


def test_windows_shutdown_codes_do_not_access_posix_signal_constants(monkeypatch):
    verifier = _verifier()
    monkeypatch.setattr(verifier, "_is_windows", lambda: True)
    monkeypatch.setattr(verifier, "signal", SimpleNamespace())
    assert verifier._accepted_shutdown_codes() == {0}


def test_native_failure_reports_the_failed_step_not_the_forced_exit_log(tmp_path, monkeypatch):
    verifier = _verifier()
    log = tmp_path / "Editarr.log"
    log.write_text("INSECURE AUTH COOKIE CONFIGURATION: development only\n")
    process = SimpleNamespace(pid=1, returncode=None)

    def stop(owned):
        owned.returncode = 1  # taskkill /F status on Windows

    def failing_step(*_args, **_kwargs):
        raise RuntimeError("setup step failed") from OSError("pipe closed")

    monkeypatch.setattr(verifier, "_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(verifier, "_program_snapshot", lambda _runtime: "unchanged")
    monkeypatch.setattr(verifier, "_start", lambda *_args: (process, log))
    monkeypatch.setattr(verifier, "_stop", stop)
    monkeypatch.setattr(verifier, "_wait_ready", failing_step)
    evidence = verifier.Evidence("windows-x64", "0", "exe", "runtime", "sha")

    with pytest.raises(RuntimeError, match="setup step failed") as raised:
        verifier._native_smoke(
            tmp_path / "Editarr.exe", tmp_path, tmp_path, evidence,
            jellyfin="http://127.0.0.1:1", test_fake_models=False,
        )

    failure = verifier._describe_failure(raised.value)
    assert failure.startswith("RuntimeError in failing_step:")
    assert "<- caused by OSError: pipe closed" in failure
    assert failure.endswith("runtime log tail: INSECURE AUTH COOKIE CONFIGURATION: "
                            "development only\n")


def test_verifier_login_retries_superseded_startup_read(monkeypatch):
    verifier = _verifier()
    responses = iter([
        (503, {}, b'{"code": "jellyfin_unavailable"}'),
        (200, {}, b'{"csrf_token": "fixture-token"}'),
    ])
    monkeypatch.setattr(verifier, "_request", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(verifier, "_POLL_WAKE", SimpleNamespace(wait=lambda _: None))

    assert verifier._login("http://localhost:3348")[1] == "fixture-token"


@pytest.mark.parametrize("code", ["jellyfin_unavailable", "different_error"])
def test_verifier_login_still_fails_on_persistent_or_unrelated_503(monkeypatch, code):
    verifier = _verifier()
    calls = []
    clock = iter([0.0, 0.0, 16.0])
    monkeypatch.setattr(verifier, "time", SimpleNamespace(monotonic=lambda: next(clock)))

    def request(*_args, **_kwargs):
        calls.append(code)
        return 503, {}, json.dumps({"code": code}).encode()

    monkeypatch.setattr(verifier, "_request", request)
    with pytest.raises(RuntimeError, match=f"HTTP 503; code='{code}'; body="):
        verifier._login("http://localhost:3348")
    assert calls == [code]


def test_failed_verification_keeps_bounded_json_evidence(tmp_path, monkeypatch):
    verifier = _verifier()
    output = tmp_path / "failure.json"
    monkeypatch.setattr(verifier, "_arguments", lambda: SimpleNamespace(
        runtime=tmp_path / "missing-runtime", target="linux-x86_64", json_out=output,
        keep_root=False,
    ))
    assert verifier.main() == 1
    evidence = json.loads(output.read_text())
    assert evidence["target"] == "linux-x86_64"
    assert "runtime directory does not exist" in evidence["failures"][0]


def test_app_runtime_identity_comes_from_embedded_runtime_inventory(tmp_path):
    verifier = _verifier()
    app = tmp_path / "Editarr.app"
    embedded = app / "Contents" / "Resources" / "runtime" / "Editarr"
    embedded.parent.mkdir(parents=True)
    expected = _runtime_inventory(embedded)

    metadata = verifier._runtime_inventory(app, "macos-arm64")

    assert metadata["version"] == expected["version"]
    assert verifier._embedded_runtime_root(app) == embedded


def test_app_bundled_helper_resolves_from_embedded_runtime(tmp_path):
    verifier = _verifier()
    app = tmp_path / "Editarr.app"
    helper = (
        app
        / "Contents"
        / "Resources"
        / "runtime"
        / "Editarr"
        / "_internal"
        / "helpers"
        / "ffmpeg"
    )
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"test helper")

    assert verifier._bundled_helper(app, "ffmpeg", "macos-arm64") == helper


def test_windows_raw_runtime_resolves_only_the_exe_entrypoint(tmp_path):
    verifier = _verifier()
    runtime = tmp_path / "Editarr"
    runtime.mkdir()
    executable = runtime / "Editarr.exe"
    executable.write_bytes(b"windows frozen executable")
    executable.chmod(0o755)

    assert verifier._executable(runtime, "windows-x64") == executable
    helper = runtime / "_internal" / "helpers" / "ffmpeg.exe"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"windows helper")
    assert verifier._bundled_helper(runtime, "ffmpeg", "windows-x64") == helper


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"format": 2}, "format"),
        ({"version": ""}, "version"),
        ({"version": "0.1.0 untrusted"}, "version"),
        ({"target": "macos-x64"}, "target"),
        ({"source_revision": ""}, "source revision"),
    ],
)
def test_runtime_inventory_rejects_malformed_identity(tmp_path, overrides, message):
    verifier = _verifier()
    runtime = tmp_path / "Editarr"
    _runtime_inventory(runtime, **overrides)

    with pytest.raises(RuntimeError, match=message):
        verifier._runtime_inventory(runtime, None)


def test_runtime_inventory_is_required_and_must_match_requested_target(tmp_path):
    verifier = _verifier()
    runtime = tmp_path / "Editarr"
    runtime.mkdir()
    with pytest.raises(RuntimeError, match="exactly one"):
        verifier._runtime_inventory(runtime, "macos-arm64")

    runtime.rmdir()
    _runtime_inventory(runtime)
    with pytest.raises(RuntimeError, match="does not match packaged target"):
        verifier._runtime_inventory(runtime, "windows-x64")


def test_target_mismatch_failure_evidence_keeps_packaged_identity(
    tmp_path, monkeypatch, capsys
):
    verifier = _verifier()
    runtime = tmp_path / "Editarr"
    expected = _runtime_inventory(runtime)
    executable = runtime / "Editarr"
    executable.write_text("test executable", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(
        verifier,
        "_arguments",
        lambda: SimpleNamespace(
            runtime=runtime,
            editarr=None,
            target="windows-x64",
            dependency_site_packages=None,
            keep_root=False,
            json_out=None,
            test_fake_models=False,
        ),
    )

    assert verifier.main() == 1
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["target"] == "macos-arm64"
    assert evidence["version"] == expected["version"]
    assert evidence["checks"]["runtime_metadata"]["target_matches_request"] is False
    assert "does not match packaged target" in evidence["failures"][0]


def test_runtime_inventory_rejects_invalid_json(tmp_path):
    verifier = _verifier()
    runtime = tmp_path / "Editarr"
    runtime.mkdir()
    (runtime / "editarr-runtime-inventory.json").write_text("not-json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not valid JSON"):
        verifier._runtime_inventory(runtime, None)


def test_evidence_emits_version_and_packaged_metadata_provenance():
    verifier = _verifier()
    evidence = verifier.Evidence(
        "macos-arm64",
        "0.1.0",
        "/runtime/Editarr",
        "/runtime",
        "a" * 64,
        source_revision="b" * 40,
        runtime_inventory_sha256="c" * 64,
    ).output()

    assert evidence["version"] == "0.1.0"
    assert evidence["target"] == "macos-arm64"
    assert evidence["source_revision"] == "b" * 40
    assert evidence["runtime_inventory_sha256"] == "c" * 64


def test_native_environment_removes_checkout_runtime_imports_and_isolates_caches(tmp_path):
    verifier = _verifier()
    data = tmp_path / "data"
    data.mkdir()
    environment = verifier._environment(tmp_path / "Editarr", None, data, native=True)

    assert "PYTHONPATH" not in environment
    if verifier._is_windows():
        expected_path = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    else:
        expected_path = "/usr/bin:/bin"
    assert environment["PATH"] == expected_path
    assert environment["EDITARR_DATA_DIR"] == str(data)
    assert environment["EDITARR_AUTH_COOKIE_SECURE"] == "0"
    assert environment["HF_HOME"].startswith(str(tmp_path))
    assert environment["HOME"].startswith(str(tmp_path))


def test_process_proof_rejects_external_interpreters_and_checkout(tmp_path):
    verifier = _verifier()
    runtime = tmp_path / "Editarr ✓"
    runtime.mkdir()
    verifier._assert_packaged_processes(
        [{"command": str(runtime / "Editarr --internal-role http-child")}], runtime
    )

    for forbidden in ("/usr/bin/python3", "node vite", "/work/.venv/bin/python"):
        try:
            verifier._assert_packaged_processes(
                [{"command": f"{runtime}/Editarr {forbidden}"}], runtime
            )
        except RuntimeError:
            pass
        else:  # pragma: no cover - preserves each external-runtime guard explicitly
            raise AssertionError(f"did not reject {forbidden}")


def test_process_proof_accepts_macos_var_alias_and_unicode_normalization(tmp_path):
    verifier = _verifier()
    runtime = Path("/var") / tmp_path.relative_to(tmp_path.anchor) / "Editarr ✓"
    ps_runtime = Path("/private") / runtime.relative_to("/")
    decomposed = unicodedata.normalize("NFD", str(ps_runtime))

    verifier._assert_packaged_processes(
        [{"command": f"{decomposed}/Editarr --internal-role http-child"}], runtime
    )


def test_evidence_never_keeps_generated_passwords():
    verifier = _verifier()
    assert "N11-only-password" not in verifier._redact("password=N11-only-password")
    assert "opaque-worker-token" not in verifier._redact(
        "Editarr --worker-token opaque-worker-token"
    )


def test_worker_control_endpoint_uses_the_short_posix_namespace(tmp_path, monkeypatch):
    verifier = _verifier()
    monkeypatch.setattr(verifier.os, "getuid", lambda: 501, raising=False)
    endpoint = verifier._worker_control_endpoint(
        tmp_path / ("long path " * 10),
        "12345678-1234-1234-1234-123456789abc",
        "opaque-worker-token",
    )

    assert endpoint.as_posix().startswith(
        "/tmp/editarr-ipc-501/editarr-worker-control/w-12345678-"
    )
    assert endpoint.suffix == ".sock"


def test_native_quit_uses_only_the_authenticated_token_derived_endpoint(tmp_path, monkeypatch):
    verifier = _verifier()
    monkeypatch.setattr(verifier, "_is_windows", lambda: False)
    monkeypatch.setattr(verifier.os, "getuid", lambda: 501, raising=False)
    token = "native-launcher-test-token"
    data = tmp_path / "data"
    state = data / "native-instance" / "instance.json"
    state.parent.mkdir(parents=True)
    endpoint = verifier._native_instance_endpoint(data, token)
    state.write_text(
        json.dumps(
            {"pid": 1234, "token": token, "endpoint": str(endpoint), "ready": True}
        ),
        encoding="utf-8",
    )
    sent: list[str] = []

    class Connection:
        def send(self, value):
            sent.append(value)

        def recv(self):
            return {"ok": True, "pid": 1234}

        def close(self):
            pass

    monkeypatch.setattr(verifier, "_connect_native_launcher", lambda actual, key: Connection())

    proof = verifier._request_native_quit(data, {1234})

    assert sent == ["quit"]
    assert proof == {"transport": "unix-domain-socket", "authenticated": True, "pid": 1234}


def test_windows_native_quit_uses_the_token_derived_named_pipe(tmp_path, monkeypatch):
    verifier = _verifier()
    monkeypatch.setattr(verifier, "_is_windows", lambda: True)
    token = "native-launcher-test-token"
    data = tmp_path / "data"
    state = data / "native-instance" / "instance.json"
    state.parent.mkdir(parents=True)
    endpoint = verifier._native_instance_endpoint(data, token)
    assert endpoint.startswith(r"\\.\pipe\EditarrLauncher-")
    state.write_text(
        json.dumps(
            {"pid": 1234, "token": token, "endpoint": endpoint, "ready": True}
        ),
        encoding="utf-8",
    )
    sent: list[str] = []

    class Connection:
        def send(self, value):
            sent.append(value)

        def recv(self):
            return {"ok": True, "pid": 1234}

        def close(self):
            pass

    monkeypatch.setattr(verifier, "_connect_windows_pipe", lambda actual, key: Connection())

    proof = verifier._request_native_quit(data, {1234})

    assert sent == ["quit"]
    assert proof == {"transport": "windows-named-pipe", "authenticated": True, "pid": 1234}


def test_stop_terminates_an_embedded_runtime_after_its_app_wrapper_exits(monkeypatch):
    verifier = _verifier()
    monkeypatch.setattr(verifier, "_is_windows", lambda: False)
    group_signals: list[tuple[int, int]] = []
    process_signals: list[tuple[int, int]] = []

    class ExitedWrapper:
        pid = 1234

        def poll(self):
            return 0

    def killpg(process_group: int, signal_number: int):
        group_signals.append((process_group, signal_number))
        if signal_number == 0:
            raise ProcessLookupError

    def kill(pid: int, signal_number: int):
        process_signals.append((pid, signal_number))
        if signal_number == 0:
            raise ProcessLookupError

    monkeypatch.setattr(verifier.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(verifier.os, "kill", kill)
    monkeypatch.setattr(
        verifier,
        "_descendant_rows",
        lambda _pid: [(1234, 0, "app wrapper"), (1235, 1234, "embedded runtime")],
    )

    verifier._stop(ExitedWrapper())

    assert group_signals == [(1234, verifier.signal.SIGTERM), (1234, 0)]
    assert process_signals == [(1235, verifier.signal.SIGTERM), (1235, 0)]


def test_worker_process_role_and_private_arguments_are_redacted():
    verifier = _verifier()
    command = (
        "/tmp/Editarr/Editarr --internal-role scan-worker "
        "--scan-id 12345678-1234-1234-1234-123456789abc --worker-token opaque-worker-token"
    )

    assert verifier._process_role(command) == "scan-worker"
    assert "opaque-worker-token" not in verifier._redact(command)


def test_process_proof_allows_only_a_worker_bound_macos_idle_preventer(tmp_path, monkeypatch):
    verifier = _verifier()
    runtime = tmp_path / "Editarr"
    runtime.mkdir()
    monkeypatch.setattr(verifier.sys, "platform", "darwin")
    roles = [
        {"pid": 101, "parent_pid": 100, "role": "scan-worker", "command": str(runtime / "Editarr")},
        {
            "pid": 102,
            "parent_pid": 101,
            "role": "macos-idle-preventer",
            "command": "caffeinate -i -w 101",
        },
    ]

    verifier._assert_packaged_processes(roles, runtime)
    roles[1]["parent_pid"] = 100
    try:
        verifier._assert_packaged_processes(roles, runtime)
    except RuntimeError as error:
        assert "idle-preventer" in str(error)
    else:  # pragma: no cover - the check must never accept an unrelated child
        raise AssertionError("accepted an unbound system helper")


def test_live_worker_waits_for_endpoint_after_queue_reservation(tmp_path, monkeypatch):
    verifier = _verifier()
    runtime = tmp_path / "Editarr"
    runtime.mkdir()
    scan_id = "12345678-1234-1234-1234-123456789abc"
    token = "opaque-worker-token"
    command = (
        f"{runtime / 'Editarr'} --internal-role scan-worker "
        f"--scan-id {scan_id} --worker-token {token}"
    )
    rows = [
        (100, 1, f"{runtime / 'Editarr'} --no-browser"),
        (101, 100, command),
    ]
    attempts = 0

    def prove(*_args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise verifier._WorkerControlUnavailable("endpoint is still starting")
        return {"authenticated": True, "pid": 101}

    monkeypatch.setattr(verifier, "_descendant_rows", lambda _pid: rows)
    monkeypatch.setattr(verifier, "_prove_worker_control", prove)
    monkeypatch.setattr(verifier._POLL_WAKE, "wait", lambda _timeout: False)

    roles, proof = verifier._live_packaged_worker(tmp_path, 100, runtime)

    assert attempts == 2
    assert proof["authenticated"] is True
    assert [role["role"] for role in roles] == ["controller", "scan-worker"]
