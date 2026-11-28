"""Model downloads: refused by default, translated to BackendUnavailable, opt-in at CLI start."""

from __future__ import annotations

from contextlib import nullcontext

import pytest

from scanner.evaluation.folder_eval import backends
from scanner.evaluation.folder_eval.backends import (
    BackendUnavailable,
    configure_model_downloads,
    create_backend,
)
from scanner.evaluation.folder_eval.decision import scan_detectors
from scanner.model_acquisition import AcquisitionError
from scanner.modeling.model_acquisition_authority import require_acquisition

DOWNLOADS_ENV = "EDITARR_WORKER_MODEL_DOWNLOADS"


class _Service:
    """Acquisition service where nothing is installed; records acquisitions."""

    def __init__(self):
        self.acquired = []

    def verified_local_artifact(self, artifact_id, verify_content=True):
        raise AcquisitionError("not_ready", f"{artifact_id} is missing")

    def acquire(self, artifact_id, **_kwargs):
        self.acquired.append(artifact_id)
        return artifact_id


@pytest.fixture
def service(monkeypatch):
    fake = _Service()
    monkeypatch.setattr("scanner.model_acquisition.get_acquisition_service", lambda: fake)
    return fake


@pytest.fixture
def policy(monkeypatch):
    """Apply configure_model_downloads; the env refusal and flag are restored afterwards."""
    # setenv records the original state, so the refusal set by the code under test is undone.
    monkeypatch.setenv(DOWNLOADS_ENV, "")
    monkeypatch.setattr(backends, "_downloads_allowed", False)
    return configure_model_downloads


def test_default_policy_refuses_downloads_process_wide(policy):
    require_acquisition(download=True)
    policy(True)
    require_acquisition(download=True)
    policy(False)
    with pytest.raises(AcquisitionError) as refused:
        require_acquisition(download=True)
    assert refused.value.code == "download_not_permitted"


def test_missing_local_artifacts_make_the_backend_unavailable(service, policy, monkeypatch):
    monkeypatch.setitem(backends.LOCAL_BACKENDS, "siglip2", lambda: pytest.fail("loaded"))
    policy(False)
    with pytest.raises(BackendUnavailable, match="siglip2-base-patch16-256.*--allow-downloads"):
        create_backend("siglip2")


def test_acquisition_failures_while_loading_make_the_backend_unavailable(
    service, policy, monkeypatch
):
    def load():
        raise AcquisitionError("download_not_permitted", "downloads refused")

    monkeypatch.setitem(backends.LOCAL_BACKENDS, "siglip2-gore", load)
    policy(True)
    with pytest.raises(BackendUnavailable, match="refused"):
        create_backend("siglip2-gore")


def test_scan_detectors_acquire_only_when_downloads_are_allowed(service, policy, monkeypatch):
    monkeypatch.setattr(
        "scanner.settings.capabilities.capability_for",
        lambda _model: type("Capability", (), {"availability": "available"})(),
    )
    backend = object.__new__(scan_detectors.ScanDetectorBackend)
    backend.video_model = "qwen3-vl-4b"
    policy(False)
    with pytest.raises(BackendUnavailable, match="not installed"):
        backend.check_available()
    assert service.acquired == []
    policy(True)
    backend.check_available()
    assert service.acquired == ["qwen3-vl-4b-instruct-4bit"]


@pytest.mark.parametrize("command", [["run", "--model", "m"], ["all", "--models", "m"]])
@pytest.mark.parametrize("flag", [[], ["--allow-downloads"]])
def test_cli_sets_the_download_policy_before_running(tmp_path, policy, command, flag, monkeypatch):
    from scanner.commands import folder_eval_cli as cli

    seen = []

    def fake_run(_layout, _model_id, **_kwargs):
        seen.append(backends.downloads_allowed())
        with pytest.raises(AcquisitionError) if not flag else nullcontext():
            require_acquisition(download=True)
        raise BackendUnavailable("unavailable: m")

    for name in ("run_model", "write_inventory", "extract_frames", "summarize", "install_viewer"):
        monkeypatch.setattr(cli, name, lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "run_model", fake_run)
    code = cli.main(["--data-root", str(tmp_path), *command, *flag])
    assert seen == [bool(flag)]
    assert code == (2 if command[0] == "run" else 0)
