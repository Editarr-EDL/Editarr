"""First-run restore through the setup-only HTTP boundary on disposable databases."""

import io
import json
import shutil
import sqlite3
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from scanner import db as database_module
from scanner.backup.activation import HOLD, REPORT
from scanner.backup.archive import export_backup
from scanner.db import get_db
from scanner.http import auth_api, backup_api, setup_restore_api
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.security import auth_policy
from scanner.security.community_identity import load_or_create_installation_identity
from scanner.settings.config import Settings
from scanner.settings.deployment_config import DeploymentSnapshot, parse_base_url
from tests.community_identity_support import identity_home
from tests.jellyfin_setup_fake import install, setup_body


def _insert_owner(database, username):
    """A Jellyfin-linked owner, the only owner a backup restores."""
    database.execute(
        "INSERT INTO users (id, role, status, username, normalized_username, local_export_id, "
        "permission_revision, created_at, updated_at) VALUES (1, 'owner', 'active', ?, ?, ?, 1, "
        "'2026-09-27', '2026-09-27')",
        (username, username.lower(), "local-" + "0" * 31 + "1"),
    )
    database.execute(
        "INSERT INTO user_provider_identities (user_id, server_id, provider_user_id, link_state, "
        "created_at, updated_at) VALUES (1, 'server', ?, 'verified', '2026-09-27', '2026-09-27')",
        ("1" * 32,),
    )


@pytest.fixture
def setup_client(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    for directory in (source, target):
        directory.mkdir(mode=0o700)
        migrate_fresh_database(directory / "scanner.db", directory)
    with sqlite3.connect(source / "scanner.db") as database:
        database.execute(
            "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, created_at, "
            "updated_at) VALUES ('item', 'server', 'media', 'Movie', 'Restored', 'x', 'x')"
        )
    configuration = Settings(data_dir=target)
    monkeypatch.setattr(database_module, "settings", configuration)
    engine = database_module.get_engine()
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(auth_api, "settings", configuration)
    monkeypatch.setattr(auth_policy, "get_session_factory", lambda: factory)
    install(monkeypatch)
    monkeypatch.setattr(
        setup_restore_api, "database_location", lambda: (target / "scanner.db", target)
    )
    monkeypatch.setattr(backup_api, "database_location", lambda: (target / "scanner.db", target))
    parsed = parse_base_url("http://localhost:8188")
    app = FastAPI()
    app.state.deployment_snapshot = DeploymentSnapshot(
        parsed.base_url,
        parsed.public_origin,
        parsed.prefix,
        8188,
        "saved",
        "default",
        None,
        False,
        None,
        (),
    )
    app.include_router(setup_restore_api.router)
    app.include_router(backup_api.router)
    app.include_router(auth_api.router)

    def fixture_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = fixture_session
    app.add_middleware(auth_policy.AuthPolicyMiddleware, configuration=configuration)
    with TestClient(app, base_url="http://localhost:8188") as client:
        yield client, source, target, engine
    engine.dispose()
    for job_id, job in list(backup_api._jobs.items()):
        if not job.activating:
            job.cancelled = True
        backup_api._jobs.pop(job_id)
    setup_restore_api._flows.clear()


def _wait(client, job_id, token):
    for _ in range(100):
        response = client.get(
            f"/api/setup-restore/jobs/{job_id}", headers={"Origin": "http://localhost:8188"}
        )
        if response.status_code == 503 and response.json()["code"] == "database_maintenance":
            time.sleep(0.05)
            continue
        if response.status_code != 200:
            return response
        status = response.json()
        if status["state"] not in ("working", "activating"):
            return status
        time.sleep(0.05)
    pytest.fail("Setup restore job did not finish")


ORIGIN = {"Origin": "http://localhost:8188"}


def _flow(client):
    flow = client.post("/api/setup-restore/flow", headers=ORIGIN)
    assert flow.status_code == 200, flow.text
    return {**ORIGIN, "X-Setup-Restore-Token": flow.json()["token"]}


def _preflight(client, headers, archive, passphrase=None):
    job_id = client.post("/api/setup-restore/jobs", headers=headers).json()["id"]
    uploaded = client.put(
        f"/api/setup-restore/jobs/{job_id}/archive", headers=headers, content=archive.read_bytes()
    )
    assert uploaded.status_code == 200
    started = client.post(
        f"/api/setup-restore/jobs/{job_id}/preflight",
        headers=headers,
        json={"passphrase": passphrase},
    )
    assert started.status_code == 200
    return job_id, _wait(client, job_id, None)


def _settings_archive(archive, output):
    """Relabel a real archive as an old settings-only backup."""
    with tarfile.open(archive, "r:") as original, tarfile.open(output, "w:") as rewritten:
        for member in original:
            content = original.extractfile(member).read()
            if member.name == "manifest.json":
                content = json.dumps({**json.loads(content), "kind": "settings"}).encode()
            info = tarfile.TarInfo(member.name)
            info.size = len(content)
            rewritten.addfile(info, io.BytesIO(content))


@pytest.mark.parametrize("encrypted", [False, True])
@pytest.mark.parametrize("include_admin", [False, True])
def test_setup_restore_brings_back_the_installation_and_its_credentials(
    setup_client, tmp_path, encrypted, include_admin
):
    if encrypted and not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("age transport not installed")
    client, source, target, engine = setup_client
    if include_admin:
        with sqlite3.connect(source / "scanner.db") as database:
            _insert_owner(database, "Admin")
            database.execute(
                "INSERT INTO api_keys (id, user_id, token_digest, created_at, token_value) "
                "VALUES (1, 1, 'restored-digest', '2026-10-22 12:00:00', 'restored-plaintext')"
            )
    source_identity = load_or_create_installation_identity(identity_home(source))
    archive = tmp_path / "backup.tar"
    passphrase = "disposable-passphrase" if encrypted else None
    export_backup(source / "scanner.db", source, archive, passphrase=passphrase)
    headers = _flow(client)
    job_id, ready = _preflight(client, headers, archive, passphrase)
    assert ready["state"] == "ready", ready
    assert ready["plan"]["administrator_included"] is include_admin
    assert ready["plan"]["source_identity"]["key_id"] == source_identity.key_id
    assert "disposable-passphrase" not in str(ready)
    assert (
        client.post(f"/api/setup-restore/jobs/{job_id}/activate", headers=headers, json={})
    ).status_code == 422
    activated = client.post(
        f"/api/setup-restore/jobs/{job_id}/activate", headers=headers, json={"confirmed": True}
    )
    assert activated.status_code == 200
    if include_admin:
        for _ in range(100):
            if backup_api._jobs[job_id].state != "activating":
                break
            time.sleep(0.05)
        assert backup_api._jobs[job_id].state == "activated"
        assert (target / REPORT).is_file()
        assert (target / HOLD).is_file()
        assert client.post("/api/setup-restore/flow", headers=ORIGIN).status_code == 409
    else:
        finished = _wait(client, job_id, None)
        assert finished["state"] == "activated", finished
        assert finished["report"] == {"move_disagreements": 0}
        refused = client.post("/api/setup-restore/flow", headers=ORIGIN)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "restore_target_has_work"
    with sqlite3.connect(target / "scanner.db") as database:
        rows = database.execute(
            "SELECT username, provider_user_id FROM users "
            "JOIN user_provider_identities ON user_id = users.id"
        ).fetchall()
        keys = database.execute("SELECT token_digest, token_value FROM api_keys").fetchall()
        assert database.execute("SELECT name FROM media_items").fetchall() == [("Restored",)]
    assert bool(rows) is include_admin
    engine.dispose()
    if include_admin:
        assert rows == [("Admin", "1" * 32)]
        assert keys == [("restored-digest", None)]
    else:
        assert client.get("/api/auth/status").json()["setup_required"] is True
        response = client.post(
            "/api/auth/setup",
            headers=ORIGIN,
            json=setup_body(),
        )
        assert response.status_code == 201, response.text
    restored = load_or_create_installation_identity(identity_home(target))
    assert restored.key_id == source_identity.key_id


@pytest.mark.parametrize(
    ("statement", "code", "named"),
    [
        (
            "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, created_at, "
            "updated_at) VALUES ('kept', 'server', 'kept', 'Movie', 'Kept', 'x', 'x')",
            "restore_target_has_work",
            "media_items",
        ),
        (
            "UPDATE mcf_severity_defaults SET severity = 'high' WHERE position = 0",
            "restore_target_has_work",
            "mcf_severity_defaults",
        ),
        (
            "UPDATE media_access_scopes SET all_sources = 0",
            "restore_target_has_work",
            "media_access_scopes",
        ),
        (
            "UPDATE alembic_version SET version_num = '9999_unknown_schema'",
            "restore_target_unsupported_schema",
            "9999_unknown_schema",
        ),
    ],
)
def test_a_target_with_work_but_no_account_is_offered_owner_setup_instead(
    setup_client, statement, code, named
):
    client, _source, target, _engine = setup_client
    with sqlite3.connect(target / "scanner.db") as database:
        database.execute(statement)
    before = (target / "scanner.db").read_bytes()
    refused = client.post("/api/setup-restore/flow", headers=ORIGIN)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == code
    assert named in refused.json()["detail"]["message"]
    assert (target / "scanner.db").read_bytes() == before
    assert client.get("/api/auth/status").json()["setup_required"] is True


@pytest.mark.parametrize(
    ("statement", "code"),
    [
        (
            "INSERT INTO jellyfin_settings (id, api_key, updated_at) VALUES (1, 'k', 'x')",
            "restore_target_has_work",
        ),
        ("UPDATE filter_settings SET revision = 7", "restore_target_has_work"),
        (
            "UPDATE alembic_version SET version_num = '9999_unknown_schema'",
            "restore_target_unsupported_schema",
        ),
    ],
)
def test_work_added_after_preflight_refuses_activation_by_name(
    setup_client, tmp_path, statement, code
):
    client, source, target, _engine = setup_client
    archive = tmp_path / "valid.backup"
    export_backup(source / "scanner.db", source, archive)
    headers = _flow(client)
    job_id, ready = _preflight(client, headers, archive)
    assert ready["state"] == "ready", ready
    with sqlite3.connect(target / "scanner.db") as database:
        database.execute(statement)
    before = (target / "scanner.db").read_bytes()
    response = client.post(
        f"/api/setup-restore/jobs/{job_id}/activate", headers=headers, json={"confirmed": True}
    )
    assert response.status_code == 200
    failed = _wait(client, job_id, None)
    assert (failed["state"], failed["reason"]) == ("failed", code)
    assert (target / "scanner.db").read_bytes() == before
    assert not (target / HOLD).exists()


def test_an_old_settings_backup_is_refused_by_name(setup_client, tmp_path):
    client, source, target, _engine = setup_client
    archive, settings = tmp_path / "full.backup", tmp_path / "settings.backup"
    export_backup(source / "scanner.db", source, archive)
    _settings_archive(archive, settings)
    _job, failed = _preflight(client, _flow(client), settings)
    assert (failed["state"], failed["reason"]) == ("failed", "restore_settings_backup")
    assert not (target / HOLD).exists()


def test_an_owner_without_a_jellyfin_link_fails_preflight_by_name(setup_client, tmp_path):
    client, source, target, _engine = setup_client
    with sqlite3.connect(source / "scanner.db") as database:
        _insert_owner(database, "Admin")
        database.execute("DELETE FROM user_provider_identities")
        database.execute("UPDATE users SET status = 'disabled', disable_cause = 'unlinked'")
    archive = tmp_path / "invalid.backup"
    export_backup(source / "scanner.db", source, archive)
    before = (target / "scanner.db").read_bytes()
    _job, failed = _preflight(client, _flow(client), archive)
    assert (failed["state"], failed["reason"]) == ("failed", "restore_owner_credential")
    assert (target / "scanner.db").read_bytes() == before
    assert not (target / HOLD).exists()


def test_setup_flow_is_origin_bound_private_and_closes_after_admin_created(setup_client):
    client, _source, target, _engine = setup_client
    assert client.post("/api/setup-restore/flow").status_code == 403
    assert (
        client.post("/api/setup-restore/flow", headers={"Origin": "http://evil.test"}).status_code
        == 403
    )
    assert client.get("/api/setup-restore/unknown", headers=ORIGIN).status_code == 423
    assert (
        client.post("/api/setup-restore/jobs/not-a-job/activate", headers=ORIGIN).status_code == 423
    )
    token = _flow(client)
    assert client.post("/api/setup-restore/jobs", headers=ORIGIN).status_code == 403
    job_id = client.post("/api/setup-restore/jobs", headers=token).json()["id"]
    assert client.get(f"/api/backups/jobs/{job_id}").status_code == 423
    other = TestClient(client.app, base_url="http://localhost:8188")
    assert other.get(f"/api/setup-restore/jobs/{job_id}", headers=ORIGIN).status_code == 403
    assert other.post("/api/setup-restore/jobs", headers=token).status_code == 403
    with sqlite3.connect(target / "scanner.db") as database:
        _insert_owner(database, "Admin")
    assert client.get(f"/api/setup-restore/jobs/{job_id}", headers=ORIGIN).status_code == 409
    assert client.post("/api/setup-restore/jobs", headers=token).status_code == 409


def test_setup_flows_are_bounded(setup_client):
    client, _source, _target, _engine = setup_client
    for _ in range(setup_restore_api._MAX_FLOWS):
        client.cookies.clear()
        assert client.post("/api/setup-restore/flow", headers=ORIGIN).status_code == 200
    client.cookies.clear()
    assert client.post("/api/setup-restore/flow", headers=ORIGIN).status_code == 429


def test_new_setup_flow_replaces_the_browsers_previous_flow(setup_client):
    client, _source, _target, _engine = setup_client
    first = _flow(client)
    job_id = client.post("/api/setup-restore/jobs", headers=first).json()["id"]
    for _ in range(setup_restore_api._MAX_FLOWS):
        second = _flow(client)
    assert len(setup_restore_api._flows) == 1
    assert backup_api._jobs[job_id].state == "cancelled"
    assert client.post("/api/setup-restore/jobs", headers=first).status_code == 403
    assert client.post("/api/setup-restore/jobs", headers=second).status_code == 200


def test_a_job_started_while_its_flow_is_replaced_is_not_orphaned(setup_client, monkeypatch):
    client, _source, _target, _engine = setup_client
    validated, resume = threading.Event(), threading.Event()
    require_empty = setup_restore_api._require_empty

    def paused(database, data_dir):
        validated.set()
        assert resume.wait(5)
        require_empty(database, data_dir)

    statuses = []
    for _ in range(backup_api._MAX_JOBS):
        old = _flow(client)
        validated.clear()
        resume.clear()
        monkeypatch.setattr(setup_restore_api, "_require_empty", paused)
        with ThreadPoolExecutor(1) as pool:
            started = pool.submit(client.post, "/api/setup-restore/jobs", headers=old)
            assert validated.wait(5)
            monkeypatch.setattr(setup_restore_api, "_require_empty", require_empty)
            _flow(client)
            resume.set()
            statuses.append(started.result().status_code)

    assert client.post("/api/setup-restore/jobs", headers=_flow(client)).status_code == 200
    assert [job.cancelled for job in backup_api._jobs.values()] == [False]
    assert statuses == [403] * backup_api._MAX_JOBS


def test_jobs_of_expired_flows_stop_counting_against_the_job_limit(setup_client):
    client, _source, _target, _engine = setup_client
    for _ in range(backup_api._MAX_JOBS):
        client.cookies.clear()
        assert client.post("/api/setup-restore/jobs", headers=_flow(client)).status_code == 200
    for owner, (csrf, _expires) in list(setup_restore_api._flows.items()):
        setup_restore_api._flows[owner] = (csrf, time.monotonic() - 1)
    client.cookies.clear()
    assert client.post("/api/setup-restore/jobs", headers=_flow(client)).status_code == 200


@pytest.mark.parametrize("encrypted", [False, True])
def test_invalid_setup_archive_can_be_cancelled_and_retried(setup_client, tmp_path, encrypted):
    if encrypted and not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("age transport not installed")
    client, source, target, _engine = setup_client
    archive = tmp_path / "archive.backup"
    if encrypted:
        export_backup(source / "scanner.db", source, archive, passphrase="disposable-passphrase")
    else:
        archive.write_bytes(b"not a backup")
    before = (target / "scanner.db").read_bytes()
    headers = _flow(client)
    job_id, failed = _preflight(client, headers, archive, "incorrect" if encrypted else None)
    assert failed["state"] == "failed"
    assert (target / "scanner.db").read_bytes() == before
    assert client.delete(f"/api/setup-restore/jobs/{job_id}", headers=headers).status_code == 200
    assert client.post("/api/setup-restore/jobs", headers=headers).status_code == 200


def test_cancelled_upload_does_not_publish_the_archive(setup_client):
    client, _source, _target, _engine = setup_client
    headers = _flow(client)
    job_id = client.post("/api/setup-restore/jobs", headers=headers).json()["id"]
    job = backup_api._jobs[job_id]

    def interrupted():
        yield b"manifest.json\n"
        job.cancelled = True
        yield b"more bytes"

    response = client.put(
        f"/api/setup-restore/jobs/{job_id}/archive", headers=headers, content=interrupted()
    )
    assert response.status_code == 400
    assert job.state == "cancelled"
    assert not job.archive.exists()
    assert not job.directory.exists()
