"""Apple Speech asset installation uses the running job's download authority."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner import db
from scanner.model_acquisition import AcquisitionError
from scanner.modeling.model_acquisition_authority import request_server
from scanner.pipeline import apple_speech
from scanner.security.access.capability_ids import Capability
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import USER
from tests.test_scan_access_http import app as app


class FakeSpeechHelper:
    def __init__(self):
        self.installed = False
        self.calls = []
        self.on_install = None

    def run(self, command, *, should_cancel):
        self.calls.append(command)
        if command[1] == "--install-assets":
            if self.on_install is not None:
                self.on_install()
            assert should_cancel() is False
            self.installed = True
        elif self.installed:
            Path(command[-1]).write_text("[]", encoding="utf-8")
        else:
            raise subprocess.CalledProcessError(
                2, command, stderr="Apple Speech assets are missing"
            )


@pytest.fixture
def native_speech(tmp_path, monkeypatch):
    monkeypatch.setattr(
        apple_speech,
        "platform",
        SimpleNamespace(
            system=lambda: "Darwin",
            machine=lambda: "arm64",
            mac_ver=lambda: ("26.0", (), "arm64"),
        ),
    )
    monkeypatch.setattr(apple_speech, "resolve_helper", lambda *a, **kw: tmp_path / "apple-speech")
    monkeypatch.delenv("EDITARR_WORKER_MODEL_DOWNLOADS", raising=False)
    monkeypatch.delenv("EDITARR_WORKER_SCAN_ID", raising=False)
    speech = FakeSpeechHelper()
    monkeypatch.setattr(apple_speech, "run_command", speech.run)
    return speech


@pytest.fixture
def queued_account(app, monkeypatch):
    scan_id, _ = app.seed_scan(USER, "apple-speech-assets")
    # This is the binding the dedicated worker passes to its spawned audio child.
    monkeypatch.setenv("EDITARR_WORKER_SCAN_ID", scan_id)
    monkeypatch.setattr(db, "get_session_factory", lambda: app.accounts.factory)
    return app.accounts


@pytest.mark.parametrize("assets_installed", [False, True])
def test_job_without_download_permission_only_uses_installed_assets(
    native_speech, queued_account, assets_installed
):
    queued_account.set_override(USER, Capability.MODELS_DOWNLOAD, OverrideEffect.DENY)
    native_speech.installed = assets_installed

    if assets_installed:
        assert apple_speech.transcribe("clip.wav", locale="en-us") == []
    else:
        with pytest.raises(AcquisitionError) as exc:
            apple_speech.transcribe("clip.wav", locale="en-us")
        assert exc.value.code == "download_not_permitted"

    assert [call[1] for call in native_speech.calls] == ["en-us"]


def test_job_with_model_download_permission_installs_and_reuses_assets(
    native_speech, queued_account
):
    queued_account.set_override(USER, Capability.MODELS_DOWNLOAD, OverrideEffect.ALLOW)

    assert apple_speech.transcribe("clip.wav", locale="en-us") == []
    assert apple_speech.transcribe("clip.wav", locale="en-us") == []

    assert [call[1] for call in native_speech.calls] == [
        "en-us", "--install-assets", "en-us", "en-us",
    ]
    assert native_speech.calls[1][1:] == ["--install-assets", "en-us"]


def test_revoked_download_permission_stops_active_installation(native_speech, queued_account):
    queued_account.set_override(USER, Capability.MODELS_DOWNLOAD, OverrideEffect.ALLOW)

    def revoke():
        queued_account.set_override(USER, Capability.MODELS_DOWNLOAD, OverrideEffect.DENY)

    native_speech.on_install = revoke

    with pytest.raises(AcquisitionError) as exc:
        apple_speech.transcribe("clip.wav", locale="en-us")

    assert exc.value.code == "download_not_permitted"
    assert [call[1] for call in native_speech.calls] == ["en-us", "--install-assets"]
    assert not native_speech.installed


def test_owner_can_install_locally_and_unbound_request_cannot(native_speech):
    with request_server(), pytest.raises(AcquisitionError) as exc:
        apple_speech.transcribe("clip.wav", locale="en-us")
    assert exc.value.code == "download_not_permitted"
    assert [call[1] for call in native_speech.calls] == ["en-us"]

    assert apple_speech.transcribe("clip.wav", locale="en-us") == []
    assert [call[1] for call in native_speech.calls] == [
        "en-us", "en-us", "--install-assets", "en-us",
    ]
