"""0024 adds account dispositions without changing existing rows."""

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

PREVIOUS = "0023_account_link_transitions"


def _at_previous(tmp_path):
    """A 0023 database with personal publications, a regeneration job, and a user."""
    path, ids, user = _publications(tmp_path, {"queued": {"regeneration": JOBS["queued"]}})
    _upgrade(path, PREVIOUS)
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT INTO account_link_transitions (user_id, actor_id, kind, state, to_server_id, "
            "to_provider_user_id, from_export_id, created_at, updated_at) VALUES (?, ?, 'link', "
            "'completed', 'server', ?, ?, '2026-01-01', '2026-01-01')",
            (user, user, "a" * 32, "local-" + "b" * 32),
        )
        other = database.execute(
            "INSERT INTO users (role, status, local_credential, local_export_id, "
            "permission_revision, created_at, updated_at) VALUES ('user', 'active', 'none', ?, 1, "
            "'2026-01-01', '2026-01-01')",
            ("local-" + "c" * 32,),
        ).lastrowid
    return path, ids, user, other


def _disposition(database, user, **values):
    row = {
        "user_id": user,
        "actor_id": None,
        "mode": "retain",
        "destination_id": None,
        "confirmation": "d" * 64,
        "impact_json": "{}",
        "result_json": "{}",
        "created_at": "2026-01-01",
    } | values
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    database.execute(
        f"INSERT INTO account_dispositions ({columns}) VALUES ({marks})", tuple(row.values())
    )


def test_previous_revision_upgrades_with_every_row_kept_and_an_empty_table(tmp_path):
    path, _ids, _user, _other = _at_previous(tmp_path)
    original, _ = _snapshot(path)
    generation = adoption.upgrade_existing_database(path, path.parent)
    assert generation.is_dir()
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert adoption.HEAD_REVISION == "0039_review_community_sources"
    upgraded, _ = _snapshot(path)
    drop_0031_additions(upgraded, original)
    assert upgraded.pop("account_dispositions") == []
    assert upgraded.pop("account_transfer_moves") == []
    assert upgraded.pop("jellyfin_user_blocks") == []
    assert all(row[-1] is None for row in upgraded["media_item_exports"])
    upgraded["media_item_exports"] = [row[:-1] for row in upgraded["media_item_exports"]]
    assert without_stable_media_identity(upgraded) == retire_publications(original, upgraded)
    assert adoption.upgrade_existing_database(path, path.parent) is None


def test_constraints_allow_one_consistent_record_per_account(tmp_path):
    path, _ids, user, other = _at_previous(tmp_path)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA foreign_keys = ON")
        for invalid in (
            {"mode": "archive"},
            {"mode": "transfer"},
            {"mode": "retain", "destination_id": other},
            {"mode": "transfer", "destination_id": user},
            {"confirmation": "D" * 64},
            {"confirmation": "d" * 63},
            {"actor_id": 999},
        ):
            with pytest.raises(sqlite3.IntegrityError):
                _disposition(database, user, **invalid)
        _disposition(database, user, mode="transfer", destination_id=other, actor_id=other)
        with pytest.raises(sqlite3.IntegrityError, match="account_dispositions.user_id"):
            _disposition(database, user, mode="delete")
        _disposition(database, other, mode="delete")
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("DELETE FROM users WHERE id = ?", (other,))
        assert database.execute(
            "SELECT user_id, mode, destination_id FROM account_dispositions ORDER BY user_id"
        ).fetchall() == [(user, "transfer", other), (other, "delete", None)]


def test_a_modified_head_schema_is_refused(tmp_path):
    path, _ids, _user, _other = _at_previous(tmp_path)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("ALTER TABLE account_dispositions ADD COLUMN extra TEXT")
    with pytest.raises(ValueError, match="(?i)unsupported|schema"):
        adoption._inspect_database(path)


def _without_move_confirmation(database):
    """Recreate `account_transfer_moves` as round 1 shipped it, before the binding."""
    created = database.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'account_transfer_moves'"
    ).fetchone()[0]
    database.execute("DROP TABLE account_transfer_moves")
    database.execute("\n".join(line for line in created.splitlines() if "confirmation" not in line))
    database.execute(
        "CREATE INDEX ix_account_transfer_moves_user ON account_transfer_moves (user_id)"
    )


def _round_zero(database):
    database.execute("DROP TABLE account_transfer_moves")
    database.execute("ALTER TABLE media_item_exports DROP COLUMN intended_policy_owner_id")


@pytest.mark.parametrize("earlier", [_round_zero, _without_move_confirmation])
def test_a_database_from_an_earlier_unmerged_0024_is_not_accepted_as_head(tmp_path, earlier):
    path, _ids, _user, _other = _at_previous(tmp_path)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        earlier(database)
    with pytest.raises(ValueError, match="(?i)unsupported|schema"):
        adoption._inspect_database(path)


@pytest.mark.parametrize("interrupted", [False, True])
def test_partial_0024_upgrade_requires_verified_recovery(tmp_path, interrupted):
    from tests.test_activity_migration import _assert_recoverable

    path, _ids, _user, _other = _at_previous(tmp_path)
    original, _ = _snapshot(path)
    script = """
import os, sys, alembic.op
from pathlib import Path
from scanner.persistence.migrations import adoption
real = alembic.op.create_table
def fail(name, *args, **kwargs):
    result = real(name, *args, **kwargs)
    if name == "account_dispositions":
        if sys.argv[3] == "True":
            os._exit(97)
        raise RuntimeError("injected 0024 failure")
    return result
alembic.op.create_table = fail
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


def test_backups_keep_dispositions_with_their_users(tmp_path):
    path, _ids, user, _other = _at_previous(tmp_path)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        _disposition(database, user, impact_json='{"reviews":1}')
    snapshot = tmp_path / "full.sqlite3"
    _sqlite_snapshot(path, snapshot)
    with sqlite3.connect(snapshot) as database:
        assert database.execute(
            "SELECT user_id, mode, impact_json FROM account_dispositions"
        ).fetchall() == [(user, "retain", '{"reviews":1}')]


def _move(database, user, **values):
    row = {
        "user_id": user,
        "confirmation": "c" * 64,
        "export_id": None,
        "old_path": "/media/a/.editarr/x/a.edl",
        "new_path": "/media/a/.editarr/y/a.edl",
        "old_sha256": "a" * 64,
        "new_sha256": "b" * 64,
        "new_file_id": None,
        "outcome": None,
    } | values
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    database.execute(
        f"INSERT INTO account_transfer_moves ({columns}) VALUES ({marks})", tuple(row.values())
    )


def test_transfer_moves_and_intended_owner_are_constrained(tmp_path):
    path, ids, user, other = _at_previous(tmp_path)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA foreign_keys = ON")
        for invalid in (
            {"new_path": "/media/a/.editarr/x/a.edl"},
            {"old_sha256": "A" * 64},
            {"new_sha256": "b" * 63},
            {"outcome": "lost"},
            {"new_sha256": None, "new_file_id": "12"},
            {"user_id": 999},
            {"export_id": 999},
            {"confirmation": None},
            {"confirmation": "C" * 64},
            {"confirmation": "c" * 63},
        ):
            with pytest.raises(sqlite3.IntegrityError):
                _move(database, user, **invalid)
        export = next(iter(ids.values()))
        _move(database, user, export_id=export, new_file_id="12", outcome="kept")
        _move(database, user, old_sha256=None, new_sha256=None)
        database.execute(
            "UPDATE media_item_exports SET intended_policy_owner_id = ? WHERE id = ?",
            (other, export),
        )
        database.execute(
            "DELETE FROM media_item_export_regenerations WHERE export_id = ?", (export,)
        )
        database.execute("DELETE FROM media_item_exports WHERE id = ?", (export,))
        assert database.execute(
            "SELECT export_id, outcome FROM account_transfer_moves ORDER BY id"
        ).fetchall() == [(None, "kept"), (None, None)]
