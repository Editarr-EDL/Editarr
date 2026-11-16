"""MU-15: revision 0017 adds account profile tables without changing any row.

Covers every supported source revision, a failed and an interrupted upgrade, a
partly applied or modified shape, an unknown revision, recovery from the verified
pre-upgrade generation, the new tables' checks, and backups that move profiles,
avatars, and the registration mode with the local accounts. Sources come from
`test_filter_rules_migration._built`, upgraded to 0016 for the newest.
"""

import json
import sqlite3
import subprocess
import sys

import pytest

from scanner.backup.archive import export_backup
from scanner.persistence.migrations import adoption
from tests.activity_migration_support import _upgrade
from tests.community_identity_migration_support import no_network as no_network
from tests.explicit_filter_modes_support import drop_0031_additions
from tests.jellyfin_sync_migration_support import drop_0018_additions
from tests.test_account_migration import _snapshot
from tests.test_activity_migration import _assert_recoverable
from tests.test_backup_activation import _fixture, _restore
from tests.test_filter_rules_migration import SEALED
from tests.test_filter_rules_migration import SOURCES as RULE_SOURCES
from tests.test_filter_rules_migration import _built as _rules_built

RULES = "0016_personal_filter_rules"
SOURCES = [*RULE_SOURCES, RULES]
NEW_TABLES = ("user_profiles", "user_avatars", "account_settings")
_TIME = "2026-10-23 12:00:00.000000"
_PROFILE = "INSERT INTO user_profiles VALUES (1, ?, NULL, 'UTC', 'en', ?, ?)"
_AVATAR = "INSERT INTO user_avatars VALUES (1, ?, ?, ?)"
_SETTINGS = "INSERT INTO account_settings (id, updated_at) VALUES (1, ?)"


def _built(tmp_path, revision):
    path = _rules_built(tmp_path, SEALED if revision == RULES else revision)
    if revision == RULES:
        _upgrade(path, RULES)
    return path


def _rows(path):
    with sqlite3.connect(path) as database:
        return {
            table: database.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in NEW_TABLES
        }


@pytest.mark.parametrize("revision", SOURCES)
def test_every_supported_revision_upgrades_with_every_row_kept(tmp_path, revision):
    """Earlier revisions change rows as their own tests describe; 0017 itself changes none."""
    path = _built(tmp_path, revision)
    original, _ = _snapshot(path)

    generation = adoption.upgrade_existing_database(path, path.parent)

    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    upgraded, _ = _snapshot(path)
    assert [upgraded.pop(table) for table in NEW_TABLES] == [[], [], []]
    drop_0018_additions(upgraded)
    drop_0031_additions(upgraded, original)
    if revision == RULES:
        assert upgraded == original
    assert adoption.upgrade_existing_database(path, path.parent) is None
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert generation.is_dir()
    assert adoption._inspect_database(recovered) == revision
    assert _snapshot(recovered)[0] == original


def test_new_tables_refuse_values_the_application_never_writes(tmp_path):
    path = _built(tmp_path, RULES)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("DELETE FROM user_profiles")
        database.execute(_PROFILE, ("Owner Name", _TIME, _TIME))
        database.execute(_AVATAR, ("a" * 64, b"\x89PNG", _TIME))
        database.execute(_SETTINGS, (_TIME,))
        for statement, values in (
            ("UPDATE user_profiles SET display_name = ''", ()),
            ("UPDATE user_profiles SET language = 'x'", ()),
            ("UPDATE user_avatars SET digest = ?", ("A" * 64,)),
            ("UPDATE user_avatars SET image = ?", (b"",)),
            ("INSERT INTO account_settings (id, updated_at) VALUES (2, ?)", (_TIME,)),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(statement, values)


def test_failure_inside_0017_blocks_admission_and_recovers(tmp_path, monkeypatch):
    import alembic.op

    path = _built(tmp_path, RULES)
    original, _ = _snapshot(path)
    real_create_table = alembic.op.create_table

    def fail(name, *args, **kwargs):
        if name == "user_avatars":
            raise RuntimeError("injected 0017 failure")
        return real_create_table(name, *args, **kwargs)

    monkeypatch.setattr(alembic.op, "create_table", fail)
    with pytest.raises(RuntimeError, match="injected 0017 failure"):
        adoption.upgrade_existing_database(path, path.parent)
    monkeypatch.setattr(alembic.op, "create_table", real_create_table)
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert (journal["status"], journal["from_revision"]) == ("failed", RULES)
    _assert_recoverable(path, original, RULES)


def test_hard_exit_inside_0017_blocks_admission_and_recovers(tmp_path):
    path = _built(tmp_path, RULES)
    original, _ = _snapshot(path)
    script = (
        "import os, sys, alembic.op; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        "real = alembic.op.create_table; "
        "alembic.op.create_table = lambda name, *a, **k: os._exit(95) "
        "if name == 'account_settings' else real(name, *a, **k); "
        "adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(path.parent)], check=False
    )
    assert result.returncode == 95
    assert json.loads((path.parent / adoption.JOURNAL_NAME).read_text())["status"] == "intent"
    _assert_recoverable(path, original, RULES)


def test_a_partly_applied_0017_shape_is_refused_without_a_snapshot(tmp_path):
    path = _built(tmp_path, RULES)
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE user_profiles (user_id INTEGER PRIMARY KEY)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption.upgrade_existing_database(path, path.parent)
    assert path.read_bytes() == before
    assert not (path.parent / adoption.JOURNAL_NAME).exists()
    assert not (path.parent / "migration-snapshots").exists()


def test_a_modified_0017_head_is_refused(tmp_path):
    path = _built(tmp_path, RULES)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("DROP TABLE account_settings")
        database.execute(
            "CREATE TABLE account_settings (id INTEGER PRIMARY KEY, "
            "registration_mode VARCHAR NOT NULL, updated_at DATETIME NOT NULL)"
        )
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption._inspect_database(path)


def test_an_unknown_revision_is_refused_without_a_snapshot(tmp_path):
    path = _built(tmp_path, RULES)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("UPDATE alembic_version SET version_num = '0018_future_revision'")
    snapshots = sorted((path.parent / "migration-snapshots").iterdir())
    with pytest.raises(ValueError, match="Unsupported database revision"):
        adoption._inspect_database(path)
    with pytest.raises(ValueError, match="Unsupported database revision"):
        adoption.upgrade_existing_database(path, path.parent)
    assert sorted((path.parent / "migration-snapshots").iterdir()) == snapshots


def test_backup_restore_moves_profiles_with_the_accounts(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    for directory, digit in ((source, "a"),):
        with sqlite3.connect(directory / "scanner.db") as database:
            database.execute("DELETE FROM user_profiles")
            database.execute(_PROFILE, (f"{directory.name} name", _TIME, _TIME))
            database.execute(_AVATAR, (digit * 64, directory.name.encode(), _TIME))
            database.execute(_SETTINGS, (_TIME,))
    expected = _rows(source / "scanner.db")
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    assert _rows(target / "scanner.db") == expected
    assert expected["account_settings"][0][0] == 1
