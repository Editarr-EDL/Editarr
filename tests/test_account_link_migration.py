"""0023 adds account-link transitions and export moves without changing existing rows."""

import json
import sqlite3
import subprocess
import sys

import pytest

from scanner.backup.archive import _sqlite_snapshot
from scanner.persistence.migrations import adoption
from tests.activity_migration_support import _upgrade
from tests.community_master_migration_support import retire_publications
from tests.explicit_filter_modes_support import drop_0031_additions
from tests.stable_media_identity_migration_support import without_stable_media_identity
from tests.test_account_migration import _snapshot
from tests.test_export_regeneration_migration import JOBS, _publications

PREVIOUS = "0022_export_regenerations"
TABLES = ("account_link_transitions", "account_link_export_moves")


def _at_previous(tmp_path):
    """A 0022 database with personal publications, a regeneration job, and a linked user."""
    path, ids, user = _publications(tmp_path, {"queued": {"regeneration": JOBS["queued"]}})
    _upgrade(path, PREVIOUS)
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT INTO user_provider_identities (user_id, server_id, provider_user_id, "
            "link_state, role_explicit, created_at, updated_at) VALUES (?, 'server', ?, "
            "'server_replaced', 0, '2026-01-01', '2026-01-01')",
            (user, "d4" + "0" * 29 + "1"),
        )
    return path, ids, user


def _transition(database, user, **values):
    row = {
        "user_id": user,
        "actor_id": user,
        "kind": "link",
        "state": "writing",
        "from_server_id": None,
        "from_provider_user_id": None,
        "to_server_id": "server",
        "to_provider_user_id": "a" * 32,
        "from_export_id": "local-" + "b" * 32,
        "failure_reason": None,
        "created_at": "2026-01-01",
        "updated_at": "2026-01-01",
    } | values
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    database.execute(
        f"INSERT INTO account_link_transitions ({columns}) VALUES ({marks})", tuple(row.values())
    )


def test_previous_revision_upgrades_with_every_row_kept_and_empty_link_tables(tmp_path):
    path, _ids, _user = _at_previous(tmp_path)
    original, _ = _snapshot(path)
    generation = adoption.upgrade_existing_database(path, path.parent)
    assert generation.is_dir()
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    upgraded, _ = _snapshot(path)
    drop_0031_additions(upgraded, original)
    added = (*TABLES, "account_dispositions", "account_transfer_moves", "jellyfin_user_blocks")
    assert [upgraded.pop(table) for table in added] == [[], [], [], [], []]
    assert all(row[-1] is None for row in upgraded["media_item_exports"])
    upgraded["media_item_exports"] = [row[:-1] for row in upgraded["media_item_exports"]]
    assert without_stable_media_identity(upgraded) == retire_publications(original, upgraded)
    assert adoption.upgrade_existing_database(path, path.parent) is None


def test_constraints_allow_one_open_transition_and_only_consistent_rows(tmp_path):
    path, ids, user = _at_previous(tmp_path)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA foreign_keys = ON")
        _transition(database, user)
        with pytest.raises(sqlite3.IntegrityError, match="account_link_transitions.user_id"):
            _transition(database, user, state="removing")
        _transition(database, user, state="rolled_back", failure_reason="interrupted")
        _transition(database, user, state="rolled_back", failure_reason="refused")
        for invalid in (
            {"state": "rolled_back", "failure_reason": "stale"},
            {"state": "completed", "kind": "relink"},
            {"state": "completed", "to_provider_user_id": "0" * 32},
            {"state": "completed", "from_export_id": "a" * 32},
            {"state": "rolled_back"},
            {"state": "completed", "failure_reason": "interrupted"},
            {
                "state": "completed",
                "kind": "relink",
                "from_server_id": "old",
                "from_provider_user_id": "c" * 32,
                "from_export_id": "d" * 32,
            },
        ):
            with pytest.raises(sqlite3.IntegrityError):
                _transition(database, user, **invalid)
        _transition(
            database,
            user,
            state="completed",
            kind="relink",
            from_server_id="old",
            from_provider_user_id="c" * 32,
            from_export_id="c" * 32,
        )
        database.execute(
            "INSERT INTO account_link_export_moves (transition_id, export_id, old_path, new_path, "
            "old_sha256, new_sha256) VALUES (1, ?, '/a', '/b', ?, NULL)",
            (ids["queued"], "e" * 64),
        )
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(
                "INSERT INTO account_link_export_moves (transition_id, old_path, new_path) "
                "VALUES (1, '/same', '/same')"
            )
        assert database.execute("SELECT requeued FROM account_link_export_moves").fetchall() == [
            (0,)
        ]
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("UPDATE account_link_export_moves SET requeued = 2")
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("UPDATE account_link_export_moves SET new_file_id = '1:2'")
        database.execute(
            "INSERT INTO account_link_export_moves (transition_id, old_path, new_path, "
            "new_sha256, new_file_id) VALUES (1, '/c', '/d', ?, '1:2')",
            ("f" * 64,),
        )
        database.execute("DELETE FROM account_link_export_moves WHERE new_file_id = '1:2'")
        database.execute("DELETE FROM media_item_exports WHERE id = ?", (ids["queued"],))
        assert database.execute("SELECT export_id FROM account_link_export_moves").fetchall() == [
            (None,)
        ]
        database.execute("DELETE FROM account_link_transitions WHERE id = 1")
        assert database.execute("SELECT count(*) FROM account_link_export_moves").fetchone() == (0,)


def test_a_modified_head_schema_is_refused(tmp_path):
    path, _ids, _user = _at_previous(tmp_path)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("DROP INDEX uq_account_link_transitions_open")
    with pytest.raises(ValueError, match="(?i)unsupported|schema"):
        adoption._inspect_database(path)


@pytest.mark.parametrize("interrupted", [False, True])
def test_partial_0023_upgrade_requires_verified_recovery(tmp_path, interrupted):
    from tests.test_activity_migration import _assert_recoverable

    path, _ids, _user = _at_previous(tmp_path)
    original, _ = _snapshot(path)
    script = """
import os, sys, alembic.op
from pathlib import Path
from scanner.persistence.migrations import adoption
real = alembic.op.create_index
def fail(name, *args, **kwargs):
    result = real(name, *args, **kwargs)
    if name == "ix_account_link_export_moves_transition":
        if sys.argv[3] == "True":
            os._exit(97)
        raise RuntimeError("injected 0023 failure")
    return result
alembic.op.create_index = fail
adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(path), str(path.parent), str(interrupted)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == (97 if interrupted else 1), result.stderr.decode()
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert journal["from_revision"] == PREVIOUS
    assert journal["status"] == ("intent" if interrupted else "failed")
    _assert_recoverable(path, original, PREVIOUS)


def test_backups_keep_link_state_with_their_users(tmp_path):
    path, ids, user = _at_previous(tmp_path)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        _transition(database, user, state="removing")
        database.execute(
            "INSERT INTO account_link_export_moves (transition_id, export_id, old_path, new_path, "
            "old_sha256, new_sha256) VALUES (1, ?, '/old', '/new', ?, ?)",
            (ids["queued"], "1" * 64, "1" * 64),
        )
    full = tmp_path / "full.sqlite3"
    _sqlite_snapshot(path, full)
    with sqlite3.connect(full) as database:
        assert database.execute(
            "SELECT state, old_path, new_path FROM account_link_transitions "
            "JOIN account_link_export_moves ON transition_id = account_link_transitions.id"
        ).fetchall() == [("removing", "/old", "/new")]
