from types import SimpleNamespace

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner import api, repository
from scanner.db import Base
from scanner.model_acquisition import AcquisitionError
from scanner.models import (
    CreditsDetectionStartRequest,
)
from scanner.security.scan_jobs.authority import JobGate
from tests.credits_api_support import _database
from tests.jellyfin_item_support import jellyfin_target
from tests.scan_access_support import owner_caller


def test_ready_qwen_queues_without_consent(monkeypatch, tmp_path):
    db = _database()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    monkeypatch.setattr(
        api,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available", device="mlx"),
    )
    monkeypatch.setattr(
        api,
        "get_acquisition_service",
        lambda: SimpleNamespace(
            status=lambda artifact_id: SimpleNamespace(
                artifact_id=artifact_id,
                state="ready",
                bytes_total=1234,
                license_spdx="Apache-2.0",
                license_status="declared",
            )
        ),
    )

    started = api.api_start_credits_detection(
        CreditsDetectionStartRequest(
            target=jellyfin_target(monkeypatch, db, video_path),
            qwen_model="qwen3-vl-4b-instruct-4bit",
        ),
        owner_caller(db),
    )

    assert started.scan.status == "queued"
    assert started.model_consent is None
    assert api._active_model_backends(db) == set()
    repository.update_credits_detection_configuration(
        db,
        started.scan.id,
        started.scan.credits_detection.configuration,
        status="acquiring_model",
        progress_message="Acquiring optional Qwen model",
    )
    assert api._active_model_backends(db) == {"qwen3_vl"}
    db.close()


def test_qwen_acquisition_progress_resumes_queue(monkeypatch, tmp_path):
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
    monkeypatch.setattr(
        api,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available", device="mlx"),
    )
    artifact = SimpleNamespace(
        artifact_id="qwen3-vl-4b-instruct-4bit",
        state="not_downloaded",
        bytes_total=1000,
        license_spdx="Apache-2.0",
        license_status="declared",
    )

    class Service:
        def status(self, _artifact_id):
            return artifact

        def acquire(self, _artifact_id, *, progress, cancelled):
            assert not cancelled()
            progress(500, 1000)

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

    api._run_credits_model_acquisition(
        JobGate(api.get_session_factory(), started.scan.id), "qwen3-vl-4b-instruct-4bit"
    )

    resumed = repository.get_scan_queue_item_by_scan_id(db, started.scan.id)
    assert resumed is not None
    assert resumed.status == "queued"
    assert resumed.progress_percent == 45.0
    db.close()


def _run_failing_acquisition(monkeypatch, tmp_path, error):
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
    monkeypatch.setattr(
        api,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available", device="mlx"),
    )
    artifact = SimpleNamespace(
        artifact_id="qwen3-vl-4b-instruct-4bit",
        state="not_downloaded",
        bytes_total=1000,
        license_spdx="Apache-2.0",
        license_status="declared",
    )

    class Service:
        def status(self, _artifact_id):
            return artifact

        def acquire(self, _artifact_id, *, progress, cancelled):
            raise error

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

    api._run_credits_model_acquisition(
        JobGate(api.get_session_factory(), started.scan.id), "qwen3-vl-4b-instruct-4bit"
    )
    return db, started.scan.id


def test_qwen_acquisition_failure_is_terminal_and_path_safe(monkeypatch, tmp_path):
    error = AcquisitionError("download_failed", f"failed at {tmp_path}")
    db, scan_id = _run_failing_acquisition(monkeypatch, tmp_path, error)

    failed = repository.get_scan(db, scan_id)
    assert failed.status == "failed"
    assert failed.error_message == "Optional model acquisition failed"
    assert str(tmp_path) not in failed.error_message
    assert repository.get_scan_queue_item_by_scan_id(db, scan_id) is None
    db.close()


def test_unexpected_qwen_acquisition_error_returns_scan_to_consent(monkeypatch, tmp_path):
    db, scan_id = _run_failing_acquisition(monkeypatch, tmp_path, RuntimeError("boom"))

    recovered = repository.get_scan(db, scan_id)
    assert recovered.status == "awaiting_model_consent"
    assert repository.get_scan_queue_item_by_scan_id(db, scan_id) is not None
    assert not api._model_tasks()
    db.close()


def test_acquisition_recovery_never_overwrites_a_racing_cancellation(monkeypatch, tmp_path):
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
    monkeypatch.setattr(
        api,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available", device="mlx"),
    )
    ready = SimpleNamespace(
        artifact_id="qwen3-vl-4b-instruct-4bit",
        state="ready",
        bytes_total=1,
        license_spdx="Apache-2.0",
        license_status="declared",
    )
    monkeypatch.setattr(
        api, "get_acquisition_service", lambda: SimpleNamespace(status=lambda _id: ready)
    )
    started = api.api_start_credits_detection(
        CreditsDetectionStartRequest(
            target=jellyfin_target(monkeypatch, db, video_path),
            qwen_model="qwen3-vl-4b-instruct-4bit",
        ),
        owner_caller(db),
    )
    scan_id = started.scan.id
    repository.update_credits_detection_configuration(
        db,
        scan_id,
        started.scan.credits_detection.configuration,
        status="acquiring_model",
        progress_message="Acquiring optional Qwen model",
    )
    queue_id = repository.get_scan_queue_item_by_scan_id(db, scan_id).queue_id
    other = factory()
    state = {"busy": False, "cancelled": False}

    def cancel_before_recovery_decides(_conn, _cursor, statement, *_args):
        if statement != "BEGIN IMMEDIATE" or state["busy"] or state["cancelled"]:
            return
        state["busy"] = True
        try:
            assert repository.cancel_scan_queue_item(other, queue_id) == (scan_id, True)
            state["cancelled"] = True
        finally:
            state["busy"] = False

    event.listen(engine, "before_cursor_execute", cancel_before_recovery_decides)
    try:
        assert repository.recover_interrupted_credits_model_acquisitions(db, scan_id) == 0
    finally:
        event.remove(engine, "before_cursor_execute", cancel_before_recovery_decides)

    assert state["cancelled"]
    db.expire_all()
    assert repository.get_scan(db, scan_id).status == "cancelling"
    other.close()
    db.close()
