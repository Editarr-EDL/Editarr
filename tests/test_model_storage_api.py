"""NATIVE-09 authenticated model-management response and conflict coverage."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner import api
from scanner.db import Base
from scanner.model_acquisition import ArtifactStatus, ModelManifest, manifest

# Direct handler calls stand in for an admin; HTTP authorization has its own tests.
_ADMIN = SimpleNamespace(may=lambda _capability: True)


class _Service:
    def __init__(self, statuses: tuple[ArtifactStatus, ...]):
        self._statuses = statuses
        self.manifest: ModelManifest = manifest()
        self.cancelled: list[str] = []
        self.removed: list[str] = []

    def list_artifacts(self):
        return self._statuses

    def status(self, artifact_id: str):
        return next(item for item in self._statuses if item.artifact_id == artifact_id)

    def request_cancel(self, artifact_id: str):
        self.manifest.artifact(artifact_id)
        self.cancelled.append(artifact_id)

    def remove(self, artifact_id: str):
        self.manifest.artifact(artifact_id)
        self.removed.append(artifact_id)
        return True


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _status(state: str = "ready") -> ArtifactStatus:
    artifact = manifest().artifact("faster-whisper-base")
    return ArtifactStatus(
        artifact_id=artifact.id,
        backend_ids=artifact.backend_ids,
        state=state,  # type: ignore[arg-type]
        bytes_downloaded=artifact.expected_size if state == "ready" else 42,
        bytes_total=artifact.expected_size,
        error_code="offline_missing" if state == "failed" else None,
        error_detail="Connect to the network and retry." if state == "failed" else None,
        source_url=artifact.source_url,
        license_spdx=artifact.license.spdx,
        license_status=artifact.license.status,
    )


def test_model_storage_is_manifest_backed_and_contains_no_cache_path(monkeypatch):
    service = _Service((_status(),))
    monkeypatch.setattr(api, "get_acquisition_service", lambda: service)

    response = api.api_model_storage()

    api.ModelStorageStatus.model_validate(response)
    assert response["artifacts"][0]["artifact_id"] == "faster-whisper-base"
    assert "cache" not in str(response).lower()
    assert "path" not in response["artifacts"][0]


def test_model_cancel_and_remove_return_stable_state_codes(monkeypatch, db):
    downloading = _Service((_status("downloading"),))
    monkeypatch.setattr(api, "get_acquisition_service", lambda: downloading)

    blocked = api.api_remove_model("faster-whisper-base", db, _ADMIN)
    assert blocked.status_code == 409
    assert b'"code":"acquisition_active"' in blocked.body

    ready = _Service((_status(),))
    monkeypatch.setattr(api, "get_acquisition_service", lambda: ready)
    cancelled = api.api_cancel_model_download("faster-whisper-base", _ADMIN)
    removed = api.api_remove_model("faster-whisper-base", db, _ADMIN)

    assert cancelled["accepted"] is True
    assert ready.cancelled == ["faster-whisper-base"]
    assert removed["removed"] is True
    assert ready.removed == ["faster-whisper-base"]


def test_unknown_model_actions_never_expose_local_files(monkeypatch):
    monkeypatch.setattr(api, "get_acquisition_service", lambda: _Service((_status(),)))

    response = api.api_cancel_model_download("not-a-model", _ADMIN)

    assert response.status_code == 404
    assert b'"code":"unknown_artifact"' in response.body
    assert str(Path.home()) not in response.body.decode()


def _artifact_status(artifact_id: str) -> ArtifactStatus:
    artifact = manifest().artifact(artifact_id)
    return ArtifactStatus(
        artifact_id=artifact.id,
        backend_ids=artifact.backend_ids,
        state="ready",
        bytes_downloaded=artifact.expected_size,
        bytes_total=artifact.expected_size,
        error_code=None,
        error_detail=None,
        source_url=artifact.source_url,
        license_spdx=artifact.license.spdx,
        license_status=artifact.license.status,
    )


def _scan(status: str, *, video_model="nudenet", qwen_model="qwen3-vl-4b-instruct-4bit",
          scan_video=True, scan_audio=False, audio_filter=None):
    return SimpleNamespace(
        status=status, scan_mode=api.ScanMode.CONTENT, scan_audio=scan_audio,
        audio_model="whisper" if scan_audio else None, audio_filter=audio_filter,
        scan_video=scan_video, video_model=video_model if scan_video else None,
        qwen_model=qwen_model, credits_detection=None,
    )


_QWEN_4B = "qwen3-vl-4b-instruct-4bit"
_QWEN_8B = "qwen3-vl-8b-instruct-4bit"


@pytest.mark.parametrize(
    ("scan", "artifact_id", "blocked"),
    [
        # Queued scans re-acquire on start, as for detector artifacts.
        (_scan("queued"), _QWEN_4B, False),
        # A running scan holds its description model whatever the detector is.
        (_scan("scanning"), _QWEN_4B, True),
        (_scan("scanning"), _QWEN_8B, False),
        (_scan("acquiring_model", video_model="qwen3-vl-4b", qwen_model=_QWEN_8B), _QWEN_8B, True),
        # Deferred descriptions run after the d1-3B detector is released, still "scanning".
        (_scan("scanning", video_model="d1-3b", qwen_model=_QWEN_8B), _QWEN_8B, True),
        (_scan("scanning", video_model="d1-3b", qwen_model=_QWEN_8B), "d1-3b", True),
        (_scan("cancelling", video_model="shieldstral"), _QWEN_4B, True),
        # The audio Qwen filter uses the same model; other audio filters do not.
        (_scan("scanning", scan_video=False, scan_audio=True, audio_filter="qwen"), _QWEN_4B, True),
        (_scan("scanning", scan_video=False, scan_audio=True, audio_filter="better_profanity"),
         _QWEN_4B, False),
        (_scan("complete"), _QWEN_4B, False),
    ],
)
def test_active_scans_protect_their_description_model_artifact(
    monkeypatch, db, scan, artifact_id, blocked
):
    service = _Service((_artifact_status(artifact_id),))
    monkeypatch.setattr(api, "get_acquisition_service", lambda: service)
    monkeypatch.setattr(api, "list_scan_queue", lambda _db: [scan])

    response = api.api_remove_model(artifact_id, db, _ADMIN)

    if blocked:
        assert response.status_code == 409
        assert b'"code":"model_in_use"' in response.body
        assert service.removed == []
    else:
        assert response["removed"] is True
