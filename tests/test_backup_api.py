"""HTTP backup export contract on disposable file-backed installations."""

import shutil
import sqlite3
import threading
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from scanner.http import backup_api
from scanner.http.background import SystemWork
from scanner.persistence.migrations.runner import migrate_fresh_database
from tests.operations_access_support import as_owner

ENGINE = SystemWork.SETUP_RESTORE


@pytest.fixture
def backup_client(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    migrate_fresh_database(source / "scanner.db", source)
    with sqlite3.connect(source / "scanner.db") as connection:
        connection.execute(
            "INSERT INTO jellyfin_settings (id, api_key, updated_at) VALUES (1, ?, ?)",
            ("source-secret-canary", "2026-09-27"),
        )
    monkeypatch.setattr(backup_api, "database_location", lambda: (source / "scanner.db", source))
    application = FastAPI()
    application.include_router(backup_api.router)
    accounts = as_owner(application, tmp_path / "access")
    with TestClient(application) as client:
        yield client, source
    accounts.close()
    for job_id, job in list(backup_api._jobs.items()):
        if not job.activating:
            backup_api.discard_backup(job_id, owner=job.owner)
        backup_api._jobs.pop(job_id)


def _wait(client, job_id):
    for _ in range(200):
        result = client.get(f"/api/backups/jobs/{job_id}")
        assert result.status_code == 200
        job = result.json()
        if job["state"] not in ("working", "activating"):
            return job
        time.sleep(0.05)
    pytest.fail("Backup job did not finish")


def test_api_plain_export_downloads_a_full_archive_without_exposing_secrets(backup_client):
    client, _source = backup_client
    created = client.post("/api/backups/jobs", json={"action": "export"})
    assert created.status_code == 200
    export = _wait(client, created.json()["id"])
    assert export["state"] == "ready", export
    assert export["kind"] == "full"
    assert "source-secret-canary" not in str(export)
    assert client.head(f"/api/backups/jobs/{export['id']}/download").status_code == 200
    response = client.get(f"/api/backups/jobs/{export['id']}/download")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "editarr-full.backup" in response.headers["content-disposition"]
    assert response.content.startswith(b"manifest.json")


@pytest.mark.parametrize(
    "body",
    [
        {"action": "export", "kind": "settings"},
        {"action": "restore"},
    ],
)
def test_settings_backups_and_in_app_restore_are_refused(backup_client, body):
    client, _source = backup_client
    assert client.post("/api/backups/jobs", json=body).status_code == 422
    assert backup_api._jobs == {}


def test_restore_routes_exist_only_during_setup(backup_client):
    client, _source = backup_client
    job_id = client.post("/api/backups/jobs", json={"action": "export"}).json()["id"]
    _wait(client, job_id)
    for method, path in (
        ("PUT", f"/api/backups/jobs/{job_id}/archive"),
        ("POST", f"/api/backups/jobs/{job_id}/preflight"),
        ("POST", f"/api/backups/jobs/{job_id}/activate"),
    ):
        assert client.request(method, path).status_code in (404, 405)


def test_api_rejects_an_unpaired_passphrase_and_cancels(backup_client):
    client, _source = backup_client
    for body in ({"encrypt": True}, {"passphrase": "orphan"}):
        assert (
            client.post("/api/backups/jobs", json={"action": "export", **body}).status_code == 400
        )
    job_id = client.post("/api/backups/jobs", json={"action": "export"}).json()["id"]
    _wait(client, job_id)
    assert client.delete(f"/api/backups/jobs/{job_id}").json()["state"] == "cancelled"
    assert client.head(f"/api/backups/jobs/{job_id}/download").status_code == 409


def test_api_encrypted_export_keeps_passphrase_private(backup_client):
    if not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("Optional source age transport not installed")
    client, _source = backup_client
    secret = "disposable-test-passphrase"
    export = client.post(
        "/api/backups/jobs", json={"action": "export", "encrypt": True, "passphrase": secret}
    ).json()
    job = _wait(client, export["id"])
    assert job["state"] == "ready", job["error"]
    assert job["inventory"]["protection"] == "age-scrypt"
    assert secret not in str(job)
    archive = client.get(f"/api/backups/jobs/{export['id']}/download").content
    assert archive.startswith(b"age-encryption.org/v1\n")


def test_cancelled_worker_keeps_directory_until_operation_settles(backup_client):
    client, _source = backup_client
    job = backup_api.new_job("export")
    directory = job.directory
    started = threading.Event()
    settle = threading.Event()

    def operation():
        started.set()
        settle.wait(5)

    backup_api._submit(job, operation, "ready", authority=ENGINE, expected_state="waiting")
    assert started.wait(2)
    try:
        assert backup_api.discard_backup(job.job_id)["state"] == "working"
        assert directory.exists()
    finally:
        settle.set()

    assert _wait(client, job.job_id)["state"] == "cancelled"
    assert not directory.exists()


def test_repeated_cancellations_release_capacity_and_private_directories(backup_client):
    client, _source = backup_client
    for _ in range(backup_api._MAX_JOBS + 1):
        job = backup_api.new_job("export")
        deleted = client.delete(f"/api/backups/jobs/{job.job_id}")
        assert deleted.status_code == 200
        assert deleted.json()["state"] == "cancelled"
        assert not job.directory.exists()


def test_setup_restore_uses_shared_admission_capacity(backup_client):
    exports = [backup_api.new_job("export") for _ in range(backup_api._MAX_JOBS - 1)]
    setup_job = backup_api.new_job("restore", owner="setup-session")
    with pytest.raises(HTTPException) as failure:
        backup_api.new_job("export")
    assert failure.value.status_code == 429
    assert backup_api.discard_backup(setup_job.job_id, owner="setup-session")["state"] == (
        "cancelled"
    )
    assert not setup_job.directory.exists()
    assert backup_api.new_job("export").state == "waiting"
    for job in exports:
        backup_api.discard_backup(job.job_id)


@pytest.mark.parametrize("state", ["cancelled", "failed"])
@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_expired_cleaned_job_returns_not_found(backup_client, state, method):
    client, _source = backup_client
    job = backup_api.new_job("export")
    if state == "cancelled":
        assert client.delete(f"/api/backups/jobs/{job.job_id}").status_code == 200
    else:
        backup_api._submit(job, lambda: 1 / 0, "ready", authority=ENGINE, expected_state="waiting")
        assert _wait(client, job.job_id)["state"] == "failed"
    assert job.state == state
    assert not job.directory.exists()
    job.created -= backup_api._LIFETIME + 1

    response = client.request(method, f"/api/backups/jobs/{job.job_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Backup job expired"
    assert job.job_id not in backup_api._jobs
