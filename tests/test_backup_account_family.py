"""An archive must hold its accounts whole before any restore (MU-40).

Synthetic installations go through the real `export_backup`, `preview_restore`,
and `activate_restore`; every assertion reads the target fresh.
"""

import hashlib
import shutil
import sqlite3

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table

from scanner.backup.account_family import (
    OWNER_COUNT,
    OWNER_CREDENTIAL,
    UNKNOWN_ACCOUNT,
    USER_REFERENCES,
)
from scanner.backup.activation import GENERATION, JOURNAL, RECOVERY, activate_restore
from scanner.backup.archive import BackupError, export_backup
from scanner.backup.restore import preview_restore
from scanner.backup.restore_target import RestoreRefused
from scanner.db import Base
from scanner.persistence.migrations.runner import migrate_fresh_database
from tests.test_activity_migration import _archive_with_database

NOW = "2026-10-29 00:00:00"
CONFIRMATION = "c" * 64
FIRST = "/media/first/Movie.edl"
SECOND = "/media/second/Movie.edl"
# Integer `*_id` columns without a foreign key that do not hold a user ID.
NOT_USER_REFERENCES: frozenset[tuple[str, str]] = frozenset()


_UNLINKED_OWNER = (
    "DELETE FROM user_provider_identities WHERE user_id = 1; "
    "UPDATE users SET status = 'disabled', disable_cause = 'unlinked' WHERE id = 1"
)


def _user(database, user_id, role, name, *, status="active"):
    """An account; the owner and every active account are linked to Jellyfin."""
    database.execute(
        "INSERT INTO users (id, role, status, username, normalized_username, local_export_id, "
        "permission_revision, created_at, updated_at, disable_cause) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (
            user_id,
            role,
            status,
            name,
            name,
            "local-" + f"{user_id:032x}",
            NOW,
            NOW,
            "local" if status == "disabled" else None,
        ),
    )
    if status == "active":
        database.execute(
            "INSERT INTO user_provider_identities (user_id, server_id, provider_user_id, "
            "link_state, created_at, updated_at) VALUES (?, 'server', ?, 'verified', ?, ?)",
            (user_id, f"{user_id:032x}", NOW, NOW),
        )


def _publication(database, export_id, user_id, path):
    database.execute(
        "INSERT OR IGNORE INTO media_items (id, server_id, jellyfin_id, item_type, name, "
        "created_at, updated_at) VALUES (?, 'server', ?, 'Movie', 'Movie', ?, ?)",
        (f"item-{export_id}", f"jf-{export_id}", NOW, NOW),
    )
    database.execute(
        "INSERT INTO media_item_exports (id, media_item_id, media_source_id, format, "
        "exported_at, scope, target_user_id, target_export_id, actor_id, policy_owner_id, "
        "original_author_id, policy_revision, destination_path) "
        "VALUES (?, ?, '', 'edl', ?, 'personal', ?, ?, ?, ?, ?, 0, ?)",
        (
            export_id,
            f"item-{export_id}",
            NOW,
            user_id,
            "local-" + f"{user_id:032x}",
            user_id,
            user_id,
            user_id,
            path,
        ),
    )


def _moves(database, user_id, export_id, old_path):
    """One open transfer move and one link move of `export_id` from `old_path`."""
    new_path = old_path.replace("Movie.edl", "moved/Movie.edl")
    database.execute(
        "INSERT INTO account_transfer_moves (user_id, confirmation, export_id, old_path, "
        "new_path) VALUES (?, ?, ?, ?, ?)",
        (user_id, CONFIRMATION, export_id, old_path, new_path),
    )
    open_transition = database.execute(
        "SELECT id FROM account_link_transitions WHERE user_id = ?", (user_id,)
    ).fetchone()
    transition = (
        open_transition[0]
        if open_transition
        else database.execute(
            "INSERT INTO account_link_transitions (user_id, kind, state, to_server_id, "
            "to_provider_user_id, from_export_id, created_at, updated_at) "
            "VALUES (?, 'link', 'writing', 'server', ?, ?, ?, ?)",
            (user_id, "a" * 32, "local-" + f"{user_id:032x}", NOW, NOW),
        ).lastrowid
    )
    database.execute(
        "INSERT INTO account_link_export_moves (transition_id, export_id, old_path, new_path) "
        "VALUES (?, ?, ?, ?)",
        (transition, export_id, old_path, new_path),
    )


def _install(directory, *, members):
    """An owner, then (with `members`) an active user and a deleted, retained user."""
    directory.mkdir(mode=0o700)
    migrate_fresh_database(directory / "scanner.db", directory)
    with sqlite3.connect(directory / "scanner.db") as database:
        _user(database, 1, "owner", f"{directory.name}-owner")
        if members:
            _user(database, 2, "user", f"{directory.name}-member")
            _user(database, 3, "user", f"{directory.name}-deleted", status="disabled")
            database.execute(
                "INSERT INTO account_dispositions (user_id, actor_id, mode, confirmation, "
                "impact_json, result_json, created_at) VALUES (3, 1, 'retain', ?, '{}', '{}', ?)",
                (CONFIRMATION, NOW),
            )
    return directory / "scanner.db"


def _source(tmp_path):
    """Owner, member, and a retained deleted account, with two in-flight moves."""
    source = _install(tmp_path / "source", members=True)
    with sqlite3.connect(source) as database:
        _publication(database, 1, 2, FIRST)
        _publication(database, 2, 2, SECOND)
        _moves(database, 2, 1, FIRST)
        _moves(database, 2, 2, SECOND)
        database.execute(
            "INSERT INTO account_transfer_moves (user_id, confirmation, export_id, old_path, "
            "new_path) VALUES (2, ?, NULL, '/media/gone/Movie.edl', '/media/gone/new.edl')",
            (CONFIRMATION,),
        )
    return source


def _export(tmp_path, source):
    archive = tmp_path / "full.backup"
    export_backup(source, source.parent, archive)
    return archive


def _restore(archive, target):
    with preview_restore(archive, target) as preview:
        return activate_restore(preview, target / "scanner.db", target, confirmed=True)


def _read(path, query):
    with sqlite3.connect(path) as database:
        return database.execute(query).fetchall()


def _family(path):
    return {
        "users": _read(path, "SELECT id, username, status FROM users ORDER BY id"),
        "dispositions": _read(path, "SELECT user_id, mode FROM account_dispositions"),
        "transfer": _read(
            path, "SELECT user_id, export_id, old_path FROM account_transfer_moves ORDER BY id"
        ),
        "link": _read(
            path, "SELECT export_id, old_path FROM account_link_export_moves ORDER BY id"
        ),
        "transitions": _read(path, "SELECT user_id, state FROM account_link_transitions"),
    }


def unclassified_columns(metadata: MetaData) -> set[tuple[str, str]]:
    """User-ID candidates the registry does not name, and stale registry entries.

    A candidate is a column with a foreign key to `users`, or an integer `*_id`
    column with no foreign key at all, which must be registered or listed in
    `NOT_USER_REFERENCES`.
    """
    candidates = set()
    for table in metadata.tables.values():
        for column in table.columns:
            references_users = any(key.column.table.name == "users" for key in column.foreign_keys)
            unchecked = (
                not column.foreign_keys
                and isinstance(column.type, Integer)
                and column.name.endswith("_id")
            )
            if references_users or unchecked:
                candidates.add((table.name, column.name))
    return (candidates - NOT_USER_REFERENCES) ^ set(USER_REFERENCES)


def test_the_registry_names_every_column_that_holds_a_user_id():
    assert unclassified_columns(Base.metadata) == set()
    assert len(USER_REFERENCES) == len(set(USER_REFERENCES))


def test_a_new_user_id_column_without_a_foreign_key_fails_the_drift_check():
    metadata = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)
    Table(
        "synthetic_reviews",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("reviewer_id", Integer),
        Column("approver_id", Integer, ForeignKey("users.id")),
    )
    assert unclassified_columns(metadata) == {
        ("synthetic_reviews", "reviewer_id"),
        ("synthetic_reviews", "approver_id"),
    }


def test_restore_keeps_every_account_record_and_move_unchanged(tmp_path):
    source = _source(tmp_path)
    archive = _export(tmp_path, source)
    target = tmp_path / "target"
    _restore(archive, target)
    assert _family(target / "scanner.db") == _family(source)
    assert _family(target / "scanner.db")["dispositions"] == [(3, "retain")]


@pytest.mark.parametrize(
    "damage,reason",
    [
        (
            "UPDATE media_item_exports SET intended_policy_owner_id = 9 WHERE id = 1",
            UNKNOWN_ACCOUNT,
        ),
        ("UPDATE media_item_export_regenerations SET policy_owner_id = 9", UNKNOWN_ACCOUNT),
        ("UPDATE users SET role = 'admin' WHERE id = 1", OWNER_COUNT),
        (_UNLINKED_OWNER, OWNER_CREDENTIAL),
    ],
)
def test_preflight_refuses_an_archive_whose_accounts_are_not_whole(tmp_path, damage, reason):
    source = _source(tmp_path)
    with sqlite3.connect(source) as database:
        database.execute(
            "INSERT INTO media_item_export_regenerations (export_id, state, attempts, "
            "policy_owner_id, policy_revision) VALUES (1, 'succeeded', 0, 2, 0)"
        )
        database.executescript(damage)
    archive = _export(tmp_path, source)
    target = tmp_path / "target"
    with pytest.raises(RestoreRefused) as refused:
        with preview_restore(archive, target):
            pass
    assert refused.value.reason == reason
    assert not target.exists() or not any(target.iterdir())


def test_a_jellyfin_linked_owner_without_a_username_passes_preflight(tmp_path):
    source = _source(tmp_path)
    with sqlite3.connect(source) as database:
        database.execute(
            "UPDATE users SET username = NULL, normalized_username = NULL WHERE id = 1"
        )
    archive = _export(tmp_path, source)
    with preview_restore(archive, tmp_path / "target") as preview:
        assert preview.plan["administrator_included"] is True


def test_an_archive_without_accounts_restores_awaiting_owner_setup(tmp_path):
    source = _install(tmp_path / "source", members=False)
    with sqlite3.connect(source) as database:
        database.execute("DELETE FROM user_provider_identities")
        database.execute("DELETE FROM users")
        database.execute(
            "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
            "created_at, updated_at) VALUES ('item', 'server', 'jf', 'Movie', 'Movie', ?, ?)",
            (NOW, NOW),
        )
    archive = _export(tmp_path, source)
    target = tmp_path / "target"
    with preview_restore(archive, target) as preview:
        assert preview.plan["administrator_included"] is False
        activate_restore(preview, target / "scanner.db", target, confirmed=True)
    assert _read(target / "scanner.db", "SELECT count(*) FROM users") == [(0,)]
    assert _read(target / "scanner.db", "SELECT id FROM media_items") == [("item",)]


def test_an_unknown_schema_is_refused_without_stamping(tmp_path):
    source = _source(tmp_path)
    archive = _export(tmp_path, source)
    future = tmp_path / "future.sqlite3"
    shutil.copy(source, future)
    with sqlite3.connect(future) as database:
        database.execute("UPDATE alembic_version SET version_num = '0099_future_revision'")
    rewritten = tmp_path / "future.backup"
    _archive_with_database(archive, future, "0099_future_revision", rewritten)
    target = tmp_path / "target"
    target.mkdir()
    migrate_fresh_database(target / "scanner.db", target)
    digest = hashlib.sha256((target / "scanner.db").read_bytes()).hexdigest()
    with pytest.raises((BackupError, ValueError)):
        with preview_restore(rewritten, target):
            pass
    assert hashlib.sha256((target / "scanner.db").read_bytes()).hexdigest() == digest
    assert not any((target / name).exists() for name in (JOURNAL, GENERATION, RECOVERY))
