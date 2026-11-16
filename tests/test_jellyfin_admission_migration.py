"""0028 turns import-only admission off with an audit record and keeps every role."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.persistence.migrations import adoption
from scanner.security.audit import query
from tests.activity_migration_support import _upgrade
from tests.test_upgrade_matrix import SOURCES

ADMISSION_SOURCES = SOURCES[
    SOURCES.index("0018_jellyfin_account_sync") : SOURCES.index("0028_jellyfin_admission")
]
_USER = (
    "INSERT INTO users (id, role, status, local_credential, local_export_id, "
    "permission_revision, created_at, updated_at) VALUES (?, ?, 'active', 'none', ?, 1, "
    "'2026-01-01', '2026-01-01')"
)
_IDENTITY = (
    "INSERT INTO user_provider_identities (user_id, server_id, provider_user_id, link_state, "
    "role_explicit, created_at, updated_at) VALUES (?, 'server', ?, 'verified', ?, "
    "'2026-01-01', '2026-01-01')"
)


def _seed(path, mode):
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT INTO account_settings (id, registration_mode, updated_at, jellyfin_admission) "
            "VALUES (1, 'managed', '2026-01-01', ?)",
            (mode,),
        )
        for user_id, role, explicit in ((2, "admin", 0), (3, "user", 1)):
            database.execute(_USER, (user_id, role, "local-" + str(user_id) * 32))
            database.execute(_IDENTITY, (user_id, str(user_id) * 32, explicit))


def _columns(path, table):
    with sqlite3.connect(path) as database:
        return {row[1] for row in database.execute(f"PRAGMA table_info({table})")}


def _admission_history(path):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as db:
            rows = [
                row
                for row in query.candidates(db, datetime.now(timezone.utc))
                if row.action == "settings.jellyfin_admission.change"
            ]
            return [(query._entry(row), row) for row in rows]
    finally:
        engine.dispose()


@pytest.mark.parametrize("revision", ADMISSION_SOURCES)
def test_import_only_becomes_first_sign_in_with_one_system_audit_event(tmp_path, revision):
    path = tmp_path / "scanner.db"
    _upgrade(path, revision)
    _seed(path, "import_only")

    _upgrade(path, "head")

    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert "jellyfin_admission" not in _columns(path, "account_settings")
    assert "role_explicit" not in _columns(path, "user_provider_identities")
    [(entry, row)] = _admission_history(path)
    assert entry is not None
    assert (entry.outcome, entry.actor_kind, entry.actor_id) == ("succeeded", "system", None)
    assert json.loads(row.details_json) == {"mode": "first_login", "previous": "import_only"}
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT id, role FROM users ORDER BY id").fetchall() == [
            (2, "admin"),
            (3, "user"),
        ]
        assert database.execute(
            "SELECT user_id, provider_user_id FROM user_provider_identities ORDER BY user_id"
        ).fetchall() == [(2, "2" * 32), (3, "3" * 32)]


def test_first_sign_in_mode_upgrades_without_an_audit_event(tmp_path):
    path = tmp_path / "scanner.db"
    _upgrade(path, "0027_jellyfin_owner")
    _seed(path, "first_login")

    _upgrade(path, "head")

    assert _admission_history(path) == []
    assert "jellyfin_admission" not in _columns(path, "account_settings")


def test_an_install_without_account_settings_upgrades_without_an_audit_event(tmp_path):
    path = tmp_path / "scanner.db"
    _upgrade(path, "0027_jellyfin_owner")

    _upgrade(path, "head")

    assert _admission_history(path) == []
