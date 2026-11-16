"""0027 lets a Jellyfin-linked owner have no local password and keeps every user row and ID."""

import sqlite3

import pytest

from tests.activity_migration_support import _upgrade

PREVIOUS = "0026_archive_manual_files"
_OWNER = (
    "INSERT INTO users (role, status, local_credential, local_export_id, permission_revision, "
    "created_at, updated_at) VALUES ('owner', ?, 'none', ?, 1, '2026-01-01', '2026-01-01')"
)
_LOCAL_OWNER = (
    "INSERT INTO users (role, status, username, normalized_username, password_hash, "
    "local_credential, local_export_id, permission_revision, created_at, updated_at, "
    "password_changed_at) VALUES ('owner', 'active', 'Owner', 'owner', 'hash', 'set', ?, 1, "
    "'2026-01-01', '2026-01-01', '2026-01-01')"
)


def _export_id(character):
    return "local-" + character * 32


def test_passwordless_owner_is_refused_before_and_allowed_after(tmp_path):
    path = tmp_path / "scanner.db"
    _upgrade(path, PREVIOUS)
    with sqlite3.connect(path) as database, pytest.raises(sqlite3.IntegrityError):
        database.execute(_OWNER, ("active", _export_id("a")))

    _upgrade(path, "0027_jellyfin_owner")

    with sqlite3.connect(path) as database:
        database.execute(_OWNER, ("active", _export_id("a")))
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("UPDATE users SET status = 'disabled' WHERE role = 'owner'")
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(_OWNER, ("active", _export_id("e")))


def test_upgrade_keeps_rows_and_the_user_id_high_water_mark(tmp_path):
    path = tmp_path / "scanner.db"
    _upgrade(path, PREVIOUS)
    with sqlite3.connect(path) as database:
        database.execute(_LOCAL_OWNER, (_export_id("b"),))
        removed = database.execute(
            "INSERT INTO users (role, status, local_credential, local_export_id, "
            "permission_revision, created_at, updated_at) VALUES ('user', 'active', 'none', ?, 1, "
            "'2026-01-01', '2026-01-01')",
            (_export_id("c"),),
        ).lastrowid
        database.execute("DELETE FROM users WHERE id = ?", (removed,))
        before = database.execute("SELECT * FROM users ORDER BY id").fetchall()

    _upgrade(path, "0027_jellyfin_owner")

    with sqlite3.connect(path) as database:
        assert database.execute("SELECT * FROM users ORDER BY id").fetchall() == before
        added = database.execute(
            "INSERT INTO users (role, status, local_credential, local_export_id, "
            "permission_revision, created_at, updated_at) VALUES ('user', 'active', 'none', ?, 1, "
            "'2026-01-01', '2026-01-01')",
            (_export_id("d"),),
        ).lastrowid
    assert added > removed
