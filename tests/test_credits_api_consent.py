from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner import api, repository
from scanner.db import Base
from scanner.model_acquisition import AcquisitionCancelled
from scanner.models import (
    CreditsDetectionStartRequest,
    CreditsModelConsentRequest,
)
from scanner.security.scan_jobs.authority import JobGate
from tests.credits_api_support import _database
from tests.jellyfin_item_support import jellyfin_target
from tests.scan_access_support import owner_caller


def test_missing_qwen_waits_for_consent_and_can_continue_without_download(
    monkeypatch, tmp_path
):
    db = _database()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    capability = SimpleNamespace(availability="available", device="mlx")
    artifact = SimpleNamespace(
        artifact_id="qwen3-vl-4b-instruct-4bit",
        state="not_downloaded",
        bytes_total=1234,
        license_spdx="Apache-2.0",
        license_status="declared",
    )
    service = SimpleNamespace(status=lambda _artifact_id: artifact)
    monkeypatch.setattr(api, "qwen_enrichment_capability", lambda: capability)
    monkeypatch.setattr(api, "get_acquisition_service", lambda: service)

    started = api.api_start_credits_detection(
        CreditsDetectionStartRequest(
            target=jellyfin_target(monkeypatch, db, video_path),
            qwen_model="qwen3-vl-4b-instruct-4bit",
        ),
        owner_caller(db),
    )

    assert started.scan.status == "awaiting_model_consent"
    assert started.model_consent.expected_size == 1234
    assert started.model_consent.license_spdx == "Apache-2.0"
    resumed = api.api_credits_detection_model_consent(
        started.scan.id,
        CreditsModelConsentRequest(decision="continue_without_qwen"),
        owner_caller(db),
    )
    assert resumed.scan.status == "queued"
    assert resumed.scan.credits_detection.configuration.qwen_model is None
    assert resumed.model_consent is None
    db.close()


def test_qwen_choices_use_frozen_target_and_acquisition_errors(monkeypatch, tmp_path):
    db = _database()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    artifact = SimpleNamespace(
        artifact_id="qwen3-vl-4b-instruct-4bit",
        state="not_downloaded",
        bytes_total=1234,
        license_spdx="Apache-2.0",
        license_status="declared",
    )
    service = SimpleNamespace(
        status=lambda artifact_id: (
            artifact
            if artifact_id == artifact.artifact_id
            else (_ for _ in ()).throw(ValueError("unsupported model"))
        )
    )
    monkeypatch.setattr(
        api,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="unavailable", device="none"),
    )
    monkeypatch.setattr(api, "get_acquisition_service", lambda: service)
    target = jellyfin_target(monkeypatch, db, video_path)

    with pytest.raises(HTTPException) as unsupported:
        api.api_start_credits_detection(
            CreditsDetectionStartRequest(target=target, qwen_model="not-a-model"), owner_caller(db)
        )
    assert unsupported.value.status_code == 400
    assert unsupported.value.detail["code"] == "invalid_credits_target"

    started = api.api_start_credits_detection(
        CreditsDetectionStartRequest(
            target=target, qwen_model="qwen3-vl-4b-instruct-4bit"
        ),
        owner_caller(db),
    )
    assert started.scan.status == "awaiting_model_consent"
    with pytest.raises(HTTPException) as unavailable:
        api.api_credits_detection_model_consent(
            started.scan.id,
            CreditsModelConsentRequest(decision="acquire"),
            owner_caller(db),
        )
    assert unavailable.value.status_code == 503
    assert unavailable.value.detail["code"] == "credits_model_acquisition_failed"
    db.close()


def test_qwen_acquisition_cancellation_settles_scan_and_queue(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    capability = SimpleNamespace(availability="available", device="mlx")
    artifact = SimpleNamespace(
        artifact_id="qwen3-vl-4b-instruct-4bit",
        state="not_downloaded",
        bytes_total=1234,
        license_spdx="Apache-2.0",
        license_status="declared",
    )

    class Service:
        def status(self, _artifact_id):
            return artifact

        def acquire(self, _artifact_id, *, progress, cancelled):
            assert cancelled()
            raise AcquisitionCancelled("qwen3-vl-4b-instruct-4bit")

    monkeypatch.setattr(api, "qwen_enrichment_capability", lambda: capability)
    monkeypatch.setattr(api, "get_acquisition_service", lambda: Service())
    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    started = api.api_start_credits_detection(
        CreditsDetectionStartRequest(
            target=jellyfin_target(monkeypatch, db, video_path),
            qwen_model="qwen3-vl-4b-instruct-4bit",
        ),
        owner_caller(db),
    )
    repository.update_credits_detection_configuration(
        db,
        started.scan.id,
        started.scan.credits_detection.configuration,
        status="acquiring_model",
        progress_message="Acquiring optional Qwen model",
    )
    assert repository.cancel_scan_queue_item(db, started.queue_id) == (
        started.scan.id,
        True,
    )

    api._run_credits_model_acquisition(
        JobGate(api.get_session_factory(), started.scan.id), "qwen3-vl-4b-instruct-4bit"
    )

    assert repository.get_scan(db, started.scan.id).status == "cancelled"
    assert repository.get_scan_queue_item_by_scan_id(db, started.scan.id) is None
    db.close()


