"""Installed recovery never leaves its service running when readiness fails (MU-45)."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import installed_recovery as recovery
from scripts import verify_installed_release as imported_verifier
from tests.test_installed_artifact_verifier import _verifier


@pytest.fixture(autouse=True)
def _no_survivors():
    imported_verifier.SURVIVING.clear()
    yield
    imported_verifier.SURVIVING.clear()


def _sleeping_child():
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True
    )


def test_failed_readiness_stops_and_waits_for_the_owned_process(tmp_path, monkeypatch):
    started = []

    def start(executable, port, cwd, environment):
        started.append(_sleeping_child())
        return started[-1], cwd / "editarr.log"

    def not_ready(origin, process):
        assert process.poll() is None
        raise RuntimeError("readiness timed out")

    monkeypatch.setattr(recovery, "_start", start)
    monkeypatch.setattr(recovery, "_wait_ready", not_ready)
    try:
        with pytest.raises(RuntimeError, match="readiness timed out"):
            with recovery._Instance(Path(sys.executable), tmp_path / "data", {}):
                pytest.fail("a service that never became ready was used")
        assert started[0].poll() is not None
    finally:
        for process in started:
            process.kill()
            process.wait()


def _main_with_smoke(tmp_path, monkeypatch, smoke, *arguments):
    verifier = _verifier()
    root = tmp_path / "scratch root"
    root.mkdir()
    executable = tmp_path / "editarr"
    executable.write_text("#!/bin/sh\n")
    monkeypatch.setattr(verifier, "tempfile", SimpleNamespace(mkdtemp=lambda prefix: str(root)))
    monkeypatch.setattr(verifier, "_legacy_smoke", smoke(verifier))
    monkeypatch.setattr(sys, "argv", ["verify", "--editarr", str(executable), *arguments])
    return verifier.main(), root


def test_scratch_is_kept_while_an_owned_process_outlives_cleanup(tmp_path, monkeypatch, capsys):
    def survived(verifier):
        def smoke(executable, dependencies, root, evidence):
            verifier.SURVIVING.append(4242)
            raise RuntimeError("verifier process group survived forced cleanup")

        return smoke

    status, root = _main_with_smoke(tmp_path, monkeypatch, survived)
    assert status == 1
    assert root.is_dir()
    assert "survived cleanup" in capsys.readouterr().err


def test_a_survivor_recorded_by_the_recovery_modules_verifier_copy_keeps_scratch(
    tmp_path, monkeypatch, capsys
):
    def survived(verifier):
        assert verifier is not imported_verifier

        def smoke(executable, dependencies, root, evidence):
            imported_verifier.SURVIVING.append(4243)
            raise RuntimeError("verifier process group survived forced cleanup")

        return smoke

    status, root = _main_with_smoke(tmp_path, monkeypatch, survived)
    assert status == 1
    assert root.is_dir()
    assert "[4243]" in capsys.readouterr().err


def test_scratch_is_removed_after_confirmed_shutdown(tmp_path, monkeypatch):
    def failed(verifier):
        def smoke(executable, dependencies, root, evidence):
            raise RuntimeError("readiness timed out")

        return smoke

    status, root = _main_with_smoke(tmp_path, monkeypatch, failed)
    assert status == 1
    assert not root.exists()


def test_scratch_with_a_read_only_runtime_copy_is_removed(tmp_path, monkeypatch):
    def protected(verifier):
        def smoke(executable, dependencies, root, evidence):
            runtime = root / "runtime"
            (runtime / "_internal").mkdir(parents=True)
            (runtime / "_internal" / "library.so").write_bytes(b"synthetic")
            verifier._make_read_only(runtime)

        return smoke

    status, root = _main_with_smoke(tmp_path, monkeypatch, protected)
    assert status == 0
    assert not root.exists()


def _failing(name):
    def fail(*_args, **_kwargs):
        raise PermissionError(f"synthetic {name} failure")

    return fail


@pytest.mark.parametrize("primary", ["passed", "failed"])
def test_a_failed_removal_is_reported_without_replacing_the_result(
    tmp_path, monkeypatch, capsys, primary
):
    def leaves_a_runtime_copy(verifier):
        def smoke(executable, dependencies, root, evidence):
            runtime = root / "Editarr verified ✓ runtime" / "_internal"
            runtime.mkdir(parents=True)
            (runtime / "library.so").write_bytes(b"x" * 4096)
            if primary == "failed":
                raise RuntimeError("readiness timed out")

        return smoke

    monkeypatch.setattr(Path, "chmod", _failing("chmod"))
    monkeypatch.setattr("shutil.rmtree", _failing("rmtree"))
    evidence = tmp_path / "evidence.json"
    status, root = _main_with_smoke(
        tmp_path, monkeypatch, leaves_a_runtime_copy, "--json-out", str(evidence)
    )
    monkeypatch.undo()

    result = json.loads(evidence.read_text())
    assert status == (0 if primary == "passed" else 1)
    assert result["failures"] == ([] if primary == "passed" else ["readiness timed out"])
    cleanup = result["checks"]["scratch_cleanup"]
    assert cleanup["removed"] is False
    assert cleanup["root"] == str(root)
    assert "synthetic chmod failure" in cleanup["reason"]
    assert cleanup["retained_bytes"] >= 4096
    assert cleanup["runtime_copy_retained"] is True
    assert root.is_dir()
    assert "including the runtime copy" in capsys.readouterr().err


def test_a_removed_root_is_reported(tmp_path, monkeypatch):
    def passes(verifier):
        return lambda executable, dependencies, root, evidence: None

    evidence = tmp_path / "evidence.json"
    status, root = _main_with_smoke(tmp_path, monkeypatch, passes, "--json-out", str(evidence))
    assert status == 0
    assert json.loads(evidence.read_text())["checks"]["scratch_cleanup"] == {"removed": True}
    assert not root.exists()
