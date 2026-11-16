"""Request-started background work runs with its initiator's live authority.

Each test holds the work after the request returns, changes the initiator's
account or capability, then releases it. The work must refuse with no effect.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future

import pytest

import scanner.api as root
from scanner.http import backup_api, model_storage_api
from scanner.model_acquisition import AcquisitionError
from scanner.modeling import model_acquisition_authority, model_acquisition_service
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.identity import AccountStatus
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import ADMIN, USER, live_accounts
from tests.operations_access_http import serve_operations
from tests.test_model_acquisition import FakeRepository, _service

DISABLE = "disable"
REVOKE = "revoke"


@pytest.fixture
def operations(monkeypatch, tmp_path):
    monkeypatch.delenv("EDITARR_WORKER_MODEL_DOWNLOADS", raising=False)
    accounts = live_accounts(tmp_path)
    app = serve_operations(monkeypatch, accounts, tmp_path)
    yield app
    app.client.close()
    backup_api._jobs.clear()
    accounts.close()


class _HeldPool:
    """Keeps submitted work until the test releases it."""

    def __init__(self):
        self.held: list = []

    def submit(self, function, *args, **kwargs):
        self.held.append(lambda: function(*args, **kwargs))
        return Future()

    def release(self) -> None:
        (work,) = self.held
        work()


def _hold_model_threads(monkeypatch) -> list[threading.Thread]:
    held: list[threading.Thread] = []
    start = threading.Thread.start

    def hold(thread):
        if thread.name.startswith("editarr-model-"):
            held.append(thread)
        else:
            start(thread)

    monkeypatch.setattr(threading.Thread, "start", hold)
    return held


def _revoke(app, user, capability, change: str) -> None:
    if change == DISABLE:
        app.accounts.set_status(user, AccountStatus.DISABLED)
    else:
        app.accounts.set_override(user, capability, OverrideEffect.DENY)


def _model_service(monkeypatch, tmp_path, repository):
    service = _service(tmp_path, repository)
    backends = lambda backend_id, **_: ("fake-model",) if backend_id == "fake_backend" else ()  # noqa: E731
    monkeypatch.setattr(root, "get_acquisition_service", lambda: service)
    monkeypatch.setattr(model_storage_api, "artifact_ids_for_backend", backends)
    monkeypatch.setattr(model_acquisition_service, "artifact_ids_for_backend", backends)
    return service


def _prefetch(app) -> None:
    response = app.client.post(
        "/api/models/prefetch", headers=app.accounts.key(USER), json={"backend_id": "fake_backend"}
    )
    assert response.status_code == 202, response.text
    assert response.json()["accepted"] is True


@pytest.mark.parametrize("change", [DISABLE, REVOKE])
def test_model_prefetch_refuses_after_its_initiator_loses_access(
    operations, monkeypatch, tmp_path, change
):
    repository = FakeRepository()
    service = _model_service(monkeypatch, tmp_path, repository)
    held = _hold_model_threads(monkeypatch)
    _prefetch(operations)
    assert len(held) == 1

    _revoke(operations, USER, C.MODELS_DOWNLOAD, change)
    held[0].run()

    assert repository.calls == []
    assert service.status("fake-model").state != "ready"
    assert not service._artifact_dir(service.manifest.artifact("fake-model")).exists()


def test_model_prefetch_stops_when_access_is_lost_mid_download(operations, monkeypatch, tmp_path):
    accounts = operations.accounts

    class RevokingRepository(FakeRepository):
        def fetch(self, artifact, artifact_file, destination, *, progress, cancelled):
            accounts.set_status(USER, AccountStatus.DISABLED)
            return super().fetch(
                artifact, artifact_file, destination, progress=progress, cancelled=cancelled
            )

    service = _model_service(monkeypatch, tmp_path, RevokingRepository())
    held = _hold_model_threads(monkeypatch)
    _prefetch(operations)
    held[0].run()

    assert service.status("fake-model").state != "ready"
    assert not service._artifact_dir(service.manifest.artifact("fake-model")).exists()


def test_model_prefetch_still_installs_for_an_allowed_initiator(operations, monkeypatch, tmp_path):
    repository = FakeRepository()
    service = _model_service(monkeypatch, tmp_path, repository)
    held = _hold_model_threads(monkeypatch)
    _prefetch(operations)
    held[0].run()

    assert service.status("fake-model").state == "ready"


def test_the_request_server_refuses_unbound_acquisition(tmp_path):
    service = _service(tmp_path, FakeRepository())
    with model_acquisition_authority.request_server(), pytest.raises(AcquisitionError) as refused:
        service.acquire("fake-model")
    assert refused.value.code == "download_not_permitted"
    assert service.status("fake-model").state != "ready"


def _start_backup(app, monkeypatch) -> _HeldPool:
    pool = _HeldPool()
    monkeypatch.setattr(backup_api, "_executor", pool)
    response = app.client.post("/api/backups/jobs", headers=app.accounts.key(ADMIN), json={})
    assert response.status_code == 200, response.text
    return pool


def _job(app):
    (job,) = backup_api._jobs.values()
    return job


@pytest.mark.parametrize("change", [DISABLE, REVOKE])
def test_backup_creation_refuses_after_its_initiator_loses_access(operations, monkeypatch, change):
    created = []
    monkeypatch.setattr(backup_api, "export_backup", lambda *args, **kwargs: created.append(args))
    pool = _start_backup(operations, monkeypatch)

    _revoke(operations, ADMIN, C.BACKUPS_CREATE, change)
    pool.release()

    job = _job(operations)
    assert created == []
    assert job.state == "failed"
    assert job.error == backup_api.INITIATOR_REFUSED
    assert not job.archive.exists()
    assert not job.directory.exists()


def test_backup_creation_discards_an_archive_when_access_is_lost_while_writing(
    operations, monkeypatch
):
    accounts = operations.accounts

    def export(_database, _data_dir, archive, **_kwargs):
        archive.write_bytes(b"archive")
        accounts.set_status(ADMIN, AccountStatus.DISABLED)
        return {
            "protection": "none",
            "included_secret_types": [],
            "omitted_environment": [],
            "identity_included": True,
        }

    monkeypatch.setattr(backup_api, "export_backup", export)
    pool = _start_backup(operations, monkeypatch)
    pool.release()

    job = _job(operations)
    assert job.state == "failed"
    assert job.error == backup_api.INITIATOR_REFUSED
    assert not job.archive.exists()


def test_backup_creation_still_completes_for_an_allowed_initiator(operations, monkeypatch):
    def export(_database, _data_dir, archive, **_kwargs):
        archive.write_bytes(b"archive")
        return {
            "protection": "none",
            "included_secret_types": [],
            "omitted_environment": [],
            "identity_included": True,
        }

    monkeypatch.setattr(backup_api, "export_backup", export)
    pool = _start_backup(operations, monkeypatch)
    pool.release()

    job = _job(operations)
    assert job.state == "ready"
    assert job.archive.read_bytes() == b"archive"
