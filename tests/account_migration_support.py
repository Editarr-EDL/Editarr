"""Row-level checks that revision 0010 carried the singleton administrator into user rows."""

import sqlite3

from tests.community_master_migration_support import (
    comparable,
    legacy_rows,
    retire_publications,
)

LEGACY_AUTH_TABLES = frozenset({"admin_users", "auth_sessions", "api_keys"})
DROPPED_BY_ARCHIVE = {"arr_settings": frozenset({"api_key"})}
"""Columns revision 0025 removes; tests/test_arr_archive_migration.py checks the removal."""


def assert_preserved_after_account_migration(path, original, columns):
    """Non-auth rows are unchanged; auth rows moved exactly as revision 0010 promises.

    ``original`` and ``columns`` are a pre-upgrade row snapshot and its legacy column
    lists. The administrator becomes owner ID 1, which 0030 disables for having no
    Jellyfin link; 0030 also deletes its password and makes its username its display
    name. Every session keeps its row but is revoked, and keys keep their digest,
    time, and value.
    """
    kept = {name: names for name, names in columns.items() if name not in LEGACY_AUTH_TABLES}
    with sqlite3.connect(path) as database:
        selected, actual, expected = {}, {}, {}
        for name, names in kept.items():
            dropped = DROPPED_BY_ARCHIVE.get(name, frozenset())
            positions = [index for index, column in enumerate(names) if column not in dropped]
            selected[name] = [names[index] for index in positions]
            rows = [tuple(row[index] for index in positions) for row in original[name]]
            expected[name] = comparable(name, selected[name], rows)
            actual[name] = legacy_rows(database, name, selected[name])
        expected = retire_publications(expected, actual, selected)
        for name in kept:
            assert actual[name] == expected[name], name
        assert "admin_users" not in {
            row[0] for row in database.execute("SELECT name FROM sqlite_master")
        }
        owners = [
            (row[0], "owner", "disabled", "unlinked", None, None, row[1], 2, row[4])
            for row in original.get("admin_users", [])
        ]
        assert (
            database.execute(
                "SELECT id, role, status, disable_cause, username, normalized_username, "
                "display_name, permission_revision, created_at FROM users "
                "LEFT JOIN user_profiles ON user_id = id ORDER BY id"
            ).fetchall()
            == owners
        )
        sessions = database.execute(
            "SELECT id, user_id, token_digest, csrf_token_digest, csrf_token_ciphertext, "
            "created_at, last_used_at, absolute_expires_at, idle_expires_at, remember_me, "
            "revoked_at FROM auth_sessions ORDER BY id"
        ).fetchall()
        assert [row[:-1] for row in sessions] == [
            row[:-1] for row in original.get("auth_sessions", [])
        ]
        assert all(row[-1] is not None for row in sessions)
        for migrated, legacy in zip(sessions, original.get("auth_sessions", [])):
            if legacy[-1] is not None:
                assert migrated[-1] == legacy[-1]
        owner = owners[0][0] if owners else None
        legacy_keys = original.get("api_keys", [])
        assert database.execute(
            "SELECT id, user_id, token_digest, created_at, token_value FROM api_keys ORDER BY id"
        ).fetchall() == [
            (row[0], owner, row[1], row[2], row[3] if len(row) > 3 else None) for row in legacy_keys
        ]
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
