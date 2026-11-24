"""MU-02: the singleton administrator upgrades into the sole owner, its key, and revoked sessions.

Revision 0030 then deletes the owner's password and disables it, since it has no Jellyfin link.
"""

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import scanner.http.auth_api as auth_api
import scanner.security.auth_policy as auth_policy
from scanner.db import get_db
from scanner.persistence.migrations import adoption, preflight
from scanner.security.api_key_crypto import load_api_key_wrap_key
from scanner.security.api_keys import authenticate_api_key, seal_plaintext_api_keys
from scanner.security.auth_repository_sessions import (
    _SESSION_DIGEST_PREFIX,
    _digest_opaque_token,
    _new_opaque_token,
)
from scanner.settings.config import Settings
from tests.account_migration_support import assert_preserved_after_account_migration
from tests.community_master_migration_support import MASTER_COLUMNS, put_back_retired
from tests.community_schema_fixture import create_unversioned_fixture
from tests.jellyfin_setup_fake import install, setup_body
from tests.test_auth_policy import _deployment_snapshot

KEY = "d" * 64
ORIGIN = {"Origin": "https://editarr.example"}
SUPPORTED = [
    None,
    "0001_current_baseline",
    "0002_scan_provenance",
    "0003_local_community",
    "0004_media_identity",
    "0005_audio_tracks",
    "0006_community_evidence",
    "0007_community_publications",
    "0008_api_key",
    "0009_readable_api_key",
]


def _text_time(value):
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def _legacy_database(path, revision="0009_readable_api_key", *, administrator=True):
    """Build a supported pre-0010 database with a live session and, from 0008, a key."""
    create_unversioned_fixture(path).dispose()
    if revision is not None:
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.begin() as connection:
                adoption._stamp(connection)
                configuration = Config()
                configuration.set_main_option(
                    "script_location", str(Path(adoption.__file__).parent)
                )
                configuration.attributes["connection"] = connection
                command.upgrade(configuration, revision)
        finally:
            engine.dispose()
    token = _new_opaque_token()
    now = datetime.now(timezone.utc)
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT INTO auth_sessions (admin_user_id, token_digest, csrf_token_digest, "
            "csrf_token_ciphertext, created_at, last_used_at, absolute_expires_at, "
            "idle_expires_at, remember_me) VALUES (1, ?, 'csrf', 'cipher', ?, ?, ?, ?, 1)",
            (
                _digest_opaque_token(token, prefix=_SESSION_DIGEST_PREFIX),
                _text_time(now),
                _text_time(now),
                _text_time(now + timedelta(days=1)),
                _text_time(now + timedelta(days=1)),
            ),
        )
        if revision in {"0008_api_key", "0009_readable_api_key"}:
            readable = revision == "0009_readable_api_key"
            database.execute(
                "INSERT INTO api_keys (id, token_digest, created_at"
                + (", token_value" if readable else "")
                + ") VALUES (1, ?, '2026-10-13'"
                + (", ?)" if readable else ")"),
                (hashlib.sha256(KEY.encode()).hexdigest(), *([KEY] if readable else [])),
            )
        if not administrator:
            database.execute("DELETE FROM auth_sessions")
            database.execute("DELETE FROM admin_users")
    return token


# 0025 drops connection keys, adds archive state, and moves Radarr and Sonarr access rows
# to `archived_media_access_rows`; 0026 moves Other Files access rows and every exception
# there and drops the exception table. Snapshots put those rows back in their source tables.
# 0028 drops the admission mode and explicit-role flag; snapshots ignore them on both sides.
# 0030 drops the password columns and the registration mode, disables an unlinked
# owner, and moves its username to an empty display name, adding a profile row if it
# had none; tests pin that owner's state on their own (test_remove_passwords_migration).
# Snapshots ignore usernames and display names, and drop a profile row left empty.
# Dedicated migration tests check the new manual provenance and worker timing columns.
# 0037 retires revision-link rows; snapshots put them back (community_master_migration_support).
_ARCHIVE_COLUMNS = {
    "arr_settings": {"api_key"},
    "media_items": {"archived_source"},
    "account_settings": {"jellyfin_admission", "registration_mode"},
    "user_provider_identities": {"role_explicit"},
    "user_profiles": {"display_name", "updated_at"},
    "scans": {"worker_elapsed_seconds", "worker_attempt_started_at"},
    "scan_findings": {"manually_added", "manually_edited"},
    "review_filters": {"manually_added", "manually_edited"},
    **MASTER_COLUMNS,
    "users": {
        "password_hash",
        "local_credential",
        "password_changed_at",
        "status",
        "disable_cause",
        "permission_revision",
        "updated_at",
        "username",
        "normalized_username",
    },
}
_ARCHIVED_ACCESS = ("media_access_scope_sources", "media_access_exceptions")
_DROPPED = {
    "media_access_exceptions": ["user_id", "source_kind", "server_key", "item_key", "granted_at"]
}


def _snapshot(path):
    with sqlite3.connect(path) as database:
        columns = {
            name: [
                column[1]
                for column in database.execute(f'PRAGMA table_info("{name}")')
                if column[1] not in _ARCHIVE_COLUMNS.get(name, ())
            ]
            for (name,) in database.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name NOT IN "
                "('alembic_version', 'archived_media_access_rows')"
            )
        }
        rows = {}
        for name, names in columns.items():
            selected = ", ".join(f'"{column}"' for column in names)
            rows[name] = database.execute(
                f'SELECT {selected} FROM "{name}" ORDER BY rowid'
            ).fetchall()
        if database.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'archived_media_access_rows'"
        ).fetchone():
            for table, names in _DROPPED.items():
                if table not in columns:
                    columns[table], rows[table] = list(names), []
            for table, payload in database.execute(
                "SELECT source_table, row_json FROM archived_media_access_rows"
            ):
                row = json.loads(payload)
                rows[table].append(tuple(row[column] for column in columns[table]))
    for table in set(_ARCHIVED_ACCESS) & rows.keys():
        rows[table].sort(key=repr)
    put_back_retired(rows, columns)
    if "user_profiles" in rows:
        rows["user_profiles"] = [row for row in rows["user_profiles"] if any(row[1:])]
    return rows, columns


@pytest.fixture
def http(monkeypatch, tmp_path):
    """Serve the real auth router and access middleware over a file database."""
    clients = []

    def connect(path):
        engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        factory = sessionmaker(bind=engine)
        configuration = Settings(data_dir=tmp_path)
        monkeypatch.setattr(auth_policy, "get_session_factory", lambda: factory)
        monkeypatch.setattr(auth_api, "settings", configuration)
        application = FastAPI()
        application.state.deployment_snapshot = _deployment_snapshot("https://editarr.example")
        application.include_router(auth_api.router)

        def database():
            with factory() as session:
                yield session

        application.dependency_overrides[get_db] = database

        @application.get("/api/protected")
        def protected():
            return {"ok": True}

        application.add_middleware(auth_policy.AuthPolicyMiddleware, configuration=configuration)
        client = TestClient(application)
        clients.append((client, engine))
        return client, factory

    yield connect
    for client, engine in clients:
        client.close()
        engine.dispose()


def _cookie(token):
    return {**ORIGIN, "Cookie": f"editarr_session={token}"}


def _sign_in(client, username, password):
    return client.post(
        "/api/auth/login", headers=ORIGIN, json={"username": username, "password": password}
    )


@pytest.mark.parametrize("revision", SUPPORTED)
def test_every_supported_revision_keeps_the_owner_and_key_and_revokes_cookies(
    tmp_path, http, revision
):
    path = tmp_path / "scanner.db"
    old_cookie = _legacy_database(path, revision)
    original, columns = _snapshot(path)

    generation = adoption.upgrade_existing_database(path, tmp_path)
    assert (generation / "snapshot.sqlite3").is_file()
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert_preserved_after_account_migration(path, original, columns)

    client, factory = http(path)
    assert client.get("/api/protected", headers=_cookie(old_cookie)).json()["code"] == (
        "authentication_required"
    )
    assert client.get("/api/auth/status", headers=_cookie(old_cookie)).json()["state"] == (
        "unauthenticated"
    )
    assert _sign_in(client, "fixture-admin", "any password").status_code == 404
    key_response = client.get("/api/protected", headers={"X-Api-Key": KEY})
    if revision in {"0008_api_key", "0009_readable_api_key"}:
        assert key_response.json()["code"] == "account_disabled"
        with factory() as db:
            # Startup seals migrated plaintext; this harness has no lifespan.
            seal_plaintext_api_keys(db, load_api_key_wrap_key(tmp_path))
            assert authenticate_api_key(db, KEY).user_id == 1
    else:
        assert key_response.json()["code"] == "invalid_api_key"


def test_activity_only_database_waits_for_one_owner_then_keeps_its_rows(
    tmp_path, http, monkeypatch
):
    path = tmp_path / "scanner.db"
    _legacy_database(path, administrator=False)
    original, columns = _snapshot(path)
    assert original["admin_users"] == [] and original["scans"]

    assert adoption.upgrade_existing_database(path, tmp_path).is_dir()
    assert_preserved_after_account_migration(path, original, columns)
    client, factory = http(path)
    for headers in ({}, {"X-Api-Key": KEY}):
        response = client.get("/api/protected", headers=headers)
        assert (response.status_code, response.json()["code"]) == (423, "setup_required")
    monkeypatch.setattr(preflight, "database_location", lambda: (path, tmp_path))
    with pytest.raises(RuntimeError, match="Owner setup is required"):
        preflight.require_owner_setup()

    install(monkeypatch)
    signed_up = client.post("/api/auth/setup", headers=ORIGIN, json=setup_body())
    assert signed_up.status_code == 201, signed_up.text
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT id, role FROM users").fetchall() == [(1, "owner")]
        assert database.execute("SELECT user_id FROM api_keys").fetchall() == [(1,)]
        for name in ("scans", "saved_reviews", "review_filters", "scan_findings"):
            names = ", ".join(f'"{column}"' for column in columns[name])
            assert (
                database.execute(f'SELECT {names} FROM "{name}" ORDER BY rowid').fetchall()
                == (original[name])
            )
    preflight.require_owner_setup()
    assert client.get("/api/protected", headers={"X-Api-Key": KEY}).status_code == 200
    assert (
        client.post(
            "/api/auth/setup",
            headers=ORIGIN,
            json=setup_body(),
        ).status_code
        == 409
    )


def _assert_recoverable(path, data_dir, original, http):
    """Admission stays closed; the verified snapshot restores and re-upgrades cleanly."""
    with pytest.raises(RuntimeError, match="unresolved"):
        preflight.inspect_schema(path, data_dir)
    with pytest.raises(RuntimeError, match="unresolved"):
        adoption.upgrade_existing_database(path, data_dir)
    recovery = data_dir / "recovery"
    recovery.mkdir()
    recovered = adoption.export_upgrade_recovery(path, data_dir, recovery / "scanner.db")
    assert adoption._inspect_database(recovered) == "0009_readable_api_key"
    assert _snapshot(recovered)[0] == original
    assert adoption.upgrade_existing_database(recovered, recovery).is_dir()
    with sqlite3.connect(recovered) as database:
        assert database.execute("SELECT id, role, status FROM users").fetchall() == [
            (1, "owner", "disabled")
        ]
    client, _ = http(recovered)
    response = client.get("/api/protected", headers={"X-Api-Key": KEY})
    assert response.json()["code"] == "account_disabled"


def test_failure_inside_0010_blocks_admission_and_recovers_from_the_snapshot(
    tmp_path, monkeypatch, http
):
    import alembic.op

    path = tmp_path / "scanner.db"
    _legacy_database(path)
    original, _ = _snapshot(path)
    real_create_index = alembic.op.create_index

    def fail_after_rebuild(name, *args, **kwargs):
        if name == "ix_auth_sessions_user_id":
            raise RuntimeError("injected 0010 failure")
        return real_create_index(name, *args, **kwargs)

    monkeypatch.setattr(alembic.op, "create_index", fail_after_rebuild)
    with pytest.raises(RuntimeError, match="injected 0010 failure"):
        adoption.upgrade_existing_database(path, tmp_path)
    monkeypatch.setattr(alembic.op, "create_index", real_create_index)
    assert json.loads((tmp_path / adoption.JOURNAL_NAME).read_text())["status"] == "failed"
    _assert_recoverable(path, tmp_path, original, http)


def test_hard_exit_inside_0010_blocks_admission_and_recovers_from_the_snapshot(tmp_path, http):
    path = tmp_path / "scanner.db"
    _legacy_database(path)
    original, _ = _snapshot(path)
    script = (
        "import os, sys, alembic.op; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        "real = alembic.op.create_index; "
        "alembic.op.create_index = lambda name, *a, **k: os._exit(91) "
        "if name == 'ix_auth_sessions_user_id' else real(name, *a, **k); "
        "adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    result = subprocess.run([sys.executable, "-c", script, str(path), str(tmp_path)], check=False)
    assert result.returncode == 91
    assert json.loads((tmp_path / adoption.JOURNAL_NAME).read_text())["status"] == "intent"
    _assert_recoverable(path, tmp_path, original, http)


def test_unknown_account_schema_is_refused_without_snapshot_or_stamp(tmp_path):
    path = tmp_path / "scanner.db"
    _legacy_database(path)
    with sqlite3.connect(path) as database:
        database.execute("ALTER TABLE admin_users ADD COLUMN role TEXT")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption.upgrade_existing_database(path, tmp_path)
    assert path.read_bytes() == before
    assert not (tmp_path / adoption.JOURNAL_NAME).exists()
    assert not list((tmp_path / "migration-snapshots").glob("snapshot-*"))
