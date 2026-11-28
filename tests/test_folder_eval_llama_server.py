"""Tests for llama-server resolution, process startup failures, and the weights location."""

from __future__ import annotations

from pathlib import Path

import pytest

from scanner.evaluation.folder_eval.backends import BackendUnavailable
from scanner.evaluation.folder_eval.decision import llama_server
from scanner.evaluation.folder_eval.decision.llama_backend import LlamaServerDecisionBackend
from scanner.evaluation.folder_eval.decision.specs import SPECS


def test_startup_crash_closes_client_and_is_not_retried(monkeypatch, tmp_path: Path):
    server = tmp_path / "llama-server"
    server.write_text("")
    server.chmod(0o755)
    monkeypatch.setenv("EDITARR_LLAMA_SERVER", str(server))
    spec = SPECS["d1-3b"]
    weights = tmp_path / spec.model_id
    weights.mkdir()
    for name in (spec.model_file, spec.mmproj_file):
        (weights / name).write_bytes(b"")
    launches = []

    class Crashed:
        pid = 0

        def poll(self):
            return 1

    def popen(*args, **kwargs):
        launches.append(args)
        return Crashed()

    monkeypatch.setattr(llama_server.subprocess, "Popen", popen)
    backend = LlamaServerDecisionBackend(spec, models_dir=tmp_path)

    for _ in range(2):
        with pytest.raises(BackendUnavailable, match="exited during startup"):
            backend.score_frames([tmp_path / "frame.jpg"])
    assert len(launches) == 1
    assert backend._server._client is None and backend._server._exit_hook is None


def _unpack_releases(models_dir: Path, *builds: int) -> None:
    for build in builds:
        binary = models_dir / f"llama.cpp-b{build}" / f"llama-b{build}" / "llama-server"
        binary.parent.mkdir(parents=True)
        binary.write_text("")
        binary.chmod(0o755)


def test_only_fast_specs_use_the_newest_unpacked_release(monkeypatch, tmp_path):
    monkeypatch.delenv("EDITARR_LLAMA_SERVER", raising=False)
    monkeypatch.setattr(llama_server.shutil, "which", lambda _name: "/opt/brew/llama-server")
    _unpack_releases(tmp_path, 12359, 12556)

    fast = LlamaServerDecisionBackend(SPECS["d1-3b-fast"], models_dir=tmp_path)
    old = LlamaServerDecisionBackend(SPECS["d1-3b"], models_dir=tmp_path)

    assert fast.server_command(1)[0].endswith("llama-b12556/llama-server")
    assert old.server_command(1)[0] == "/opt/brew/llama-server"
    assert all(
        spec.bundled_server == spec.model_id.endswith(("-fast", "-f512", "-full4", "-gore", "-sev"))
        for spec in SPECS.values()
    )


def test_configured_server_must_be_executable(monkeypatch, tmp_path):
    server = tmp_path / "llama-server"
    server.write_text("")
    monkeypatch.setenv("EDITARR_LLAMA_SERVER", str(server))
    backend = LlamaServerDecisionBackend(SPECS["d1-3b-fast"], models_dir=tmp_path)

    assert llama_server.server_binary(tmp_path, bundled=True) is None
    with pytest.raises(BackendUnavailable, match="llama-server not found or not executable"):
        backend.check_available()


def test_popen_failure_is_cached_as_unavailable(monkeypatch, tmp_path):
    server = tmp_path / "llama-server"
    server.write_text("")
    server.chmod(0o755)
    monkeypatch.setenv("EDITARR_LLAMA_SERVER", str(server))
    spec = SPECS["d1-3b"]
    (tmp_path / spec.model_id).mkdir()
    for name in (spec.model_file, spec.mmproj_file):
        (tmp_path / spec.model_id / name).write_bytes(b"")
    launches = []

    def popen(*args, **kwargs):
        launches.append(args)
        raise OSError(8, "Exec format error")

    monkeypatch.setattr(llama_server.subprocess, "Popen", popen)
    backend = LlamaServerDecisionBackend(spec, models_dir=tmp_path)

    for _ in range(2):
        with pytest.raises(BackendUnavailable, match="cannot start llama-server.*Exec format"):
            backend.score_frames([tmp_path / "frame.jpg"])
    assert len(launches) == 1


@pytest.mark.parametrize(
    ("version", "label"),
    [
        ("1.0.0-dev (build 12556, commit 50518b64c)", "llama.cpp b12556"),
        ("unknown version", "llama.cpp unknown version"),
    ],
)
def test_runtime_label(version, label):
    assert llama_server.runtime_label(version) == label


def test_weights_default_to_editarrs_model_cache_unless_configured(monkeypatch, tmp_path):
    from scanner.settings.config import settings

    monkeypatch.delenv(llama_server.MODELS_DIR_ENV, raising=False)
    monkeypatch.setattr(settings, "model_cache", tmp_path / "cache")
    assert llama_server.models_dir() == tmp_path / "cache" / "evaluation" / "llama-cpp"
    assert LlamaServerDecisionBackend(SPECS["d1-3b"]).weights_dir == (
        tmp_path / "cache" / "evaluation" / "llama-cpp" / "d1-3b"
    )
    monkeypatch.setenv(llama_server.MODELS_DIR_ENV, str(tmp_path / "weights"))
    assert llama_server.models_dir() == tmp_path / "weights"
