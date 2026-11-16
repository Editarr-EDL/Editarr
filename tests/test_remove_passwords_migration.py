"""0030 deletes every password, disables accounts without a Jellyfin link, and keeps their data."""

import sqlite3

import pytest

from scanner.backup.account_family import USER_REFERENCES
from scanner.persistence.migrations import adoption
from tests.activity_migration_support import _upgrade
from tests.community_schema_fixture import SYNTHETIC_PASSWORD_HASH
from tests.test_upgrade_matrix import SOURCES as MATRIX_SOURCES
from tests.test_upgrade_matrix import _built

ACCOUNTS = "0010_user_accounts"
PROFILES = "0017_user_profiles"
SYNC = "0018_jellyfin_account_sync"
_TIME = "2026-01-01 00:00:00.000000"
_HASH = SYNTHETIC_PASSWORD_HASH
SOURCES = MATRIX_SOURCES[: MATRIX_SOURCES.index("0030_remove_passwords")]


def _columns(database, table):
    return [row[1] for row in database.execute(f'PRAGMA table_info("{table}")')]


def _insert(database, table, values):
    present = {name: value for name, value in values.items() if name in _columns(database, table)}
    return database.execute(
        f'INSERT INTO "{table}" ({", ".join(present)}) VALUES ({", ".join("?" * len(present))})',
        tuple(present.values()),
    ).lastrowid


def _member(database, user_id, role, status, *, credential="set", cause=None):
    _insert(
        database,
        "users",
        {
            "id": user_id,
            "role": role,
            "status": status,
            "username": f"member-{user_id}",
            "normalized_username": f"member-{user_id}",
            "password_hash": None if credential == "none" else _HASH,
            "local_credential": credential,
            "local_export_id": "local-" + str(user_id) * 32,
            "permission_revision": 3,
            "created_at": _TIME,
            "updated_at": _TIME,
            "password_changed_at": None if credential == "none" else _TIME,
            "disable_cause": cause,
        },
    )


def _link(database, user_id):
    _insert(
        database,
        "user_provider_identities",
        {
            "user_id": user_id,
            "server_id": "server",
            "provider_user_id": f"{user_id:x}" * 32,
            "link_state": "verified",
            "role_explicit": 0,
            "created_at": _TIME,
            "updated_at": _TIME,
        },
    )


def _seed_members(path, revision):
    """Add a linked admin, two password-only members, and a disabled one.

    A removed account leaves the `users` AUTOINCREMENT high-water mark above every row.
    """
    cause = "local" if revision >= SYNC else None
    with sqlite3.connect(path) as database:
        _member(database, 2, "admin", "active")
        _link(database, 2)
        _member(database, 3, "user", "active")
        _member(database, 4, "user", "active", credential="change_required")
        _member(database, 5, "user", "disabled", cause=cause)
        _member(database, 9, "user", "active", credential="none")
        database.execute("DELETE FROM users WHERE id = 9")
        if "account_settings" in {
            name for (name,) in database.execute("SELECT name FROM sqlite_master")
        }:
            database.execute(
                "INSERT OR IGNORE INTO account_settings (id, registration_mode, updated_at) "
                "VALUES (1, 'open', ?)",
                (_TIME,),
            )
            database.execute("UPDATE account_settings SET registration_mode = 'open'")
            if "jellyfin_read_generation" in _columns(database, "account_settings"):
                database.execute("UPDATE account_settings SET jellyfin_read_generation = 4")
        if revision >= PROFILES:
            database.executemany(
                "INSERT INTO user_profiles (user_id, display_name, email, updated_at) "
                "VALUES (?, ?, ?, ?)",
                [(3, "Three", None, _TIME), (4, None, "four@example.test", _TIME)],
            )


def _references(path):
    """Each user-keyed column's per-account row counts, for every table that holds one."""
    with sqlite3.connect(path) as database:
        tables = {name for (name,) in database.execute("SELECT name FROM sqlite_master")}
        return {
            (table, column): database.execute(
                f'SELECT "{column}", count(*) FROM "{table}" WHERE "{column}" IS NOT NULL '
                f'GROUP BY "{column}" ORDER BY "{column}"'
            ).fetchall()
            for table, column in USER_REFERENCES
            if table in tables and column in _columns(database, table)
        }


def _accounts(path):
    with sqlite3.connect(path) as database:
        return database.execute(
            "SELECT id, role, status, disable_cause, permission_revision FROM users ORDER BY id"
        ).fetchall()


@pytest.mark.parametrize("revision", SOURCES)
def test_upgrade_deletes_passwords_and_disables_unlinked_accounts(tmp_path, revision):
    path = _built(tmp_path, revision)
    accounts = revision is not None and revision >= ACCOUNTS
    if accounts:
        _seed_members(path, revision)
    references = _references(path)
    kept = _kept(path) if accounts else {}
    assert b"$argon2" in path.read_bytes()

    adoption.upgrade_existing_database(path, path.parent)

    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert b"$argon2" not in path.read_bytes()
    with sqlite3.connect(path) as database:
        assert {"password_hash", "local_credential", "password_changed_at"}.isdisjoint(
            _columns(database, "users")
        )
        assert "registration_mode" not in _columns(database, "account_settings")
    owner_revision = kept[1][0] if accounts else 1
    expected = [(1, "owner", "disabled", "unlinked", owner_revision + 1)]
    if accounts:
        expected += [
            (2, "admin", "active", None, 3),
            (3, "user", "disabled", "unlinked", 4),
            (4, "user", "disabled", "unlinked", 4),
            (5, "user", "disabled", "local", 3),
        ]
    assert _accounts(path) == expected
    assert {user_id: row[1:] for user_id, row in _kept(path).items() if accounts} == {
        user_id: row[1:] for user_id, row in kept.items()
    }
    after = _references(path)
    added = (("user_profiles", "user_id"), ("user_filter_preferences", "user_id"))
    assert {key: after[key] for key in references if key not in added} == {
        key: rows for key, rows in references.items() if key not in added
    }
    accounts_kept = [(user_id, 1) for user_id, *_ in expected]
    assert after[("user_filter_preferences", "user_id")] == accounts_kept


@pytest.mark.parametrize("revision", [r for r in SOURCES if r is not None and r >= ACCOUNTS])
def test_upgrade_moves_unlinked_usernames_to_empty_display_names(tmp_path, revision):
    path = _built(tmp_path, revision)
    _seed_members(path, revision)
    with sqlite3.connect(path) as database:
        (owner_name,) = database.execute("SELECT username FROM users WHERE id = 1").fetchone()
        owner_display = (
            database.execute("SELECT display_name FROM user_profiles WHERE user_id = 1").fetchone()
            if revision >= PROFILES
            else None
        )

    _upgrade(path, "head")

    profiled = revision >= PROFILES
    assert _names(path) == {
        1: (None, None, (owner_display or (None,))[0] or owner_name),
        2: ("member-2", "member-2", None),
        3: (None, None, "Three" if profiled else "member-3"),
        4: (None, None, "member-4"),
        5: (None, None, "member-5"),
    }
    with sqlite3.connect(path) as database:
        email = database.execute("SELECT email FROM user_profiles WHERE user_id = 4").fetchone()
        assert email == (("four@example.test",) if profiled else (None,))
        database.execute(
            "INSERT INTO users (role, status, username, normalized_username, local_export_id, "
            "permission_revision, created_at, updated_at) "
            "VALUES ('user', 'active', 'member-3', 'member-3', ?, 1, ?, ?)",
            ("local-" + "b" * 32, _TIME, _TIME),
        )


def _names(path):
    with sqlite3.connect(path) as database:
        return {
            row[0]: row[1:]
            for row in database.execute(
                "SELECT id, username, normalized_username, display_name FROM users "
                "LEFT JOIN user_profiles ON user_profiles.user_id = users.id ORDER BY id"
            )
        }


def _kept(path):
    with sqlite3.connect(path) as database:
        return {
            row[0]: row[1:]
            for row in database.execute(
                "SELECT id, permission_revision, local_export_id, created_at FROM users"
            )
        }


@pytest.mark.parametrize("revision", [ACCOUNTS, SOURCES[-1]])
def test_upgrade_keeps_the_user_id_high_water_mark_and_settings(tmp_path, revision):
    path = _built(tmp_path, revision)
    _seed_members(path, revision)

    _upgrade(path, "head")

    with sqlite3.connect(path) as database:
        assert (
            database.execute("SELECT seq FROM sqlite_sequence WHERE name = 'users'").fetchone()[0]
            >= 9
        )
        added = _insert(
            database,
            "users",
            {
                "role": "user",
                "status": "disabled",
                "disable_cause": "local",
                "local_export_id": "local-" + "a" * 32,
                "permission_revision": 1,
                "created_at": _TIME,
                "updated_at": _TIME,
            },
        )
        settings = database.execute(
            "SELECT id, jellyfin_read_generation FROM account_settings"
        ).fetchall()
    assert added == 10
    assert settings == ([(1, 4 if revision >= SYNC else 0)] if revision >= "0017" else [])


def test_a_linked_owner_stays_active_and_only_an_unlinked_owner_may_be_disabled(tmp_path):
    path = _built(tmp_path, SOURCES[-1])
    with sqlite3.connect(path) as database:
        _link(database, 1)
        before = database.execute(
            "SELECT id, role, status, disable_cause, permission_revision, updated_at FROM users"
        ).fetchall()

    _upgrade(path, "head")

    with sqlite3.connect(path) as database:
        assert (
            database.execute(
                "SELECT id, role, status, disable_cause, permission_revision, updated_at FROM users"
            ).fetchall()
            == before
        )
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("UPDATE users SET status = 'disabled', disable_cause = 'local'")
        database.execute("UPDATE users SET status = 'disabled', disable_cause = 'unlinked'")


def _session(database, session_id, user_id):
    _insert(
        database,
        "auth_sessions",
        {
            "id": session_id,
            "user_id": user_id,
            "token_digest": f"token-{session_id}",
            "csrf_token_digest": f"csrf-{session_id}",
            "csrf_token_ciphertext": f"sealed-{session_id}",
            "created_at": _TIME,
            "last_used_at": _TIME,
            "absolute_expires_at": "2099-01-01 00:00:00.000000",
            "remember_me": 1,
        },
    )


@pytest.mark.parametrize(
    "revision", [source for source in SOURCES if source and source >= ACCOUNTS]
)
def test_upgrade_revokes_every_session_a_local_password_could_have_opened(tmp_path, revision):
    path = _built(tmp_path, revision)
    _seed_members(path, revision)
    with sqlite3.connect(path) as database:
        _member(database, 6, "user", "active", credential="none")
        _link(database, 6)
        for session_id, user_id in ((11, 2), (12, 3), (13, 4), (14, 6)):
            _session(database, session_id, user_id)

    _upgrade(path, "head")

    with sqlite3.connect(path) as database:
        live = database.execute(
            "SELECT id FROM auth_sessions WHERE revoked_at IS NULL ORDER BY id"
        ).fetchall()
    assert live == [(14,)]
