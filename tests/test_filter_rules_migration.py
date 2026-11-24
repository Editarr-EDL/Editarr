"""MU-14: revision 0016 adds live rule overrides and personal rules without changing any row.

Covers every supported source revision, a failed and an interrupted upgrade, a
partly applied or modified shape, an unknown revision, recovery from the verified
pre-upgrade generation, the new tables' checks, and backups that move the new rows
with the local accounts. Sources come from `mu06-community-fixture-1` through
`test_api_key_migration._with_key` (a plaintext key), upgraded to 0015 for the newest.
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
from tests.community_master_migration_support import retire_publications
from tests.explicit_filter_modes_support import drop_0031_additions
from tests.jellyfin_sync_migration_support import drop_0018_additions
from tests.stable_media_identity_migration_support import without_stable_media_identity
from tests.test_account_migration import _snapshot
from tests.test_activity_migration import _assert_recoverable
from tests.test_api_key_migration import MEDIA_ACCESS, _with_key
from tests.test_api_key_migration import SOURCES as KEY_SOURCES
from tests.test_backup_activation import _fixture, _restore

SEALED = "0015_sealed_api_keys"
SOURCES = [*KEY_SOURCES, SEALED]
NEW_TABLES = ("user_filter_rule_overrides", "user_filter_personal_rules")
_RULE = "11111111-1111-4111-8111-111111111111"
_PREFERENCE = "INSERT OR IGNORE INTO user_filter_preferences VALUES (1, 'live', 0, 0, ?)"
_OVERRIDE = "INSERT INTO user_filter_rule_overrides VALUES (1, ?, {})"
_PERSONAL = "INSERT INTO user_filter_personal_rules VALUES (1, ?, ?, {})"
_TIME = "2026-10-22 12:00:00.000000"


def _built(tmp_path, revision):
    path, _ = _with_key(tmp_path, MEDIA_ACCESS if revision == SEALED else revision)
    if revision == SEALED:
        _upgrade(path, SEALED)
    return path


def _rows(path, table):
    with sqlite3.connect(path) as database:
        return database.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()


@pytest.mark.parametrize("revision", SOURCES)
def test_every_supported_revision_upgrades_with_every_row_kept(tmp_path, revision):
    """Earlier revisions change rows as their own tests describe; 0016 itself changes none."""
    path = _built(tmp_path, revision)
    original, _ = _snapshot(path)

    generation = adoption.upgrade_existing_database(path, path.parent)

    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    upgraded, _ = _snapshot(path)
    assert [upgraded.pop(table) for table in NEW_TABLES] == [[], []]
    # Revision 0017 adds three empty account profile tables.
    for table in ("user_profiles", "user_avatars", "account_settings"):
        assert upgraded.pop(table) == []
    drop_0018_additions(upgraded)
    drop_0031_additions(upgraded, original)
    if revision == SEALED:
        assert without_stable_media_identity(upgraded) == retire_publications(original, upgraded)
    assert adoption.upgrade_existing_database(path, path.parent) is None
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert generation.is_dir()
    assert adoption._inspect_database(recovered) == revision
    assert _snapshot(recovered)[0] == original


def test_failure_inside_0016_blocks_admission_and_recovers(tmp_path, monkeypatch):
    import alembic.op

    path = _built(tmp_path, SEALED)
    original, _ = _snapshot(path)
    real_create_table = alembic.op.create_table

    def fail(name, *args, **kwargs):
        if name == "user_filter_personal_rules":
            raise RuntimeError("injected 0016 failure")
        return real_create_table(name, *args, **kwargs)

    monkeypatch.setattr(alembic.op, "create_table", fail)
    with pytest.raises(RuntimeError, match="injected 0016 failure"):
        adoption.upgrade_existing_database(path, path.parent)
    monkeypatch.setattr(alembic.op, "create_table", real_create_table)
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert (journal["status"], journal["from_revision"]) == ("failed", SEALED)
    _assert_recoverable(path, original, SEALED)


def test_hard_exit_inside_0016_blocks_admission_and_recovers(tmp_path):
    path = _built(tmp_path, SEALED)
    original, _ = _snapshot(path)
    script = (
        "import os, sys, alembic.op; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        "real = alembic.op.create_table; "
        "alembic.op.create_table = lambda name, *a, **k: os._exit(95) "
        "if name == 'user_filter_personal_rules' else real(name, *a, **k); "
        "adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(path.parent)], check=False
    )
    assert result.returncode == 95
    assert json.loads((path.parent / adoption.JOURNAL_NAME).read_text())["status"] == "intent"
    _assert_recoverable(path, original, SEALED)


def test_a_partly_applied_0016_shape_is_refused_without_a_snapshot(tmp_path):
    path = _built(tmp_path, SEALED)
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE user_filter_rule_overrides (user_id INTEGER)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption.upgrade_existing_database(path, path.parent)
    assert path.read_bytes() == before
    assert not (path.parent / adoption.JOURNAL_NAME).exists()
    assert not (path.parent / "migration-snapshots").exists()


@pytest.mark.parametrize("table", NEW_TABLES)
def test_a_modified_0016_head_is_refused(tmp_path, table):
    path = _built(tmp_path, SEALED)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute(f"DROP TABLE {table}")
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption._inspect_database(path)


def test_an_unknown_revision_is_refused_without_a_snapshot(tmp_path):
    path = _built(tmp_path, SEALED)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("UPDATE alembic_version SET version_num = '0017_future_revision'")
    snapshots = sorted((path.parent / "migration-snapshots").iterdir())
    with pytest.raises(ValueError, match="Unsupported database revision"):
        adoption._inspect_database(path)
    with pytest.raises(ValueError, match="Unsupported database revision"):
        adoption.upgrade_existing_database(path, path.parent)
    assert sorted((path.parent / "migration-snapshots").iterdir()) == snapshots


_ACCEPTED_OVERRIDES = [
    "1, NULL, NULL, NULL, NULL, NULL, NULL, NULL",
    "0, 'allow', 'audio', 'language', 'swearing', 'x', 'low', 'audio'",
    "1, 'deny', NULL, NULL, NULL, NULL, 'high', NULL",
]
_REFUSED_OVERRIDES = [
    "0, NULL, NULL, NULL, NULL, NULL, NULL, NULL",
    "1, NULL, 'audio', NULL, NULL, NULL, NULL, NULL",
    "0, 'block', NULL, NULL, NULL, NULL, 'low', NULL",
    "0, 'deny', NULL, NULL, NULL, NULL, NULL, NULL",
    "0, 'deny', 'video', NULL, NULL, NULL, NULL, NULL",
    "0, 'deny', NULL, 'nudity', 'swearing', NULL, NULL, NULL",
    "0, 'deny', NULL, NULL, 'swearing', NULL, NULL, NULL",
    "0, 'deny', NULL, NULL, NULL, NULL, 'extreme', NULL",
    "0, 'deny', NULL, NULL, NULL, NULL, NULL, 'left'",
]
_REFUSED_PERSONAL = [
    "NULL, NULL, NULL, NULL, NULL, NULL, NULL",
    "'deny', NULL, NULL, NULL, NULL, NULL, NULL",
    "'allow', NULL, 'nudity', 'swearing', NULL, NULL, NULL",
    "'maybe', 'audio', NULL, NULL, NULL, NULL, NULL",
]


def _head(tmp_path):
    path = _built(tmp_path, SEALED)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute(_PREFERENCE, (_TIME,))
    return path


def test_the_new_tables_keep_the_instance_rule_checks(tmp_path):
    path = _head(tmp_path)
    with sqlite3.connect(path) as database:
        for number, values in enumerate(_ACCEPTED_OVERRIDES):
            database.execute(_OVERRIDE.format(values), (f"accepted-{number}",))
        database.execute(
            _PERSONAL.format("'deny', NULL, 'language', 'swearing', NULL, NULL, NULL"), (_RULE, 0)
        )
        for number, values in enumerate(_REFUSED_OVERRIDES):
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(_OVERRIDE.format(values), (f"refused-{number}",))
        for number, values in enumerate(_REFUSED_PERSONAL):
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(_PERSONAL.format(values), (f"refused-{number}", number + 1))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            database.execute(
                _PERSONAL.format("'deny', 'audio', NULL, NULL, NULL, NULL, NULL"), ("second", 0)
            )
    assert len(_rows(path, "user_filter_rule_overrides")) == len(_ACCEPTED_OVERRIDES)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION


def test_an_orphaned_override_fails_the_startup_foreign_key_check(tmp_path):
    path = _head(tmp_path)
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT INTO user_filter_rule_overrides (user_id, rule_id, disabled) VALUES (99, ?, 1)",
            (_RULE,),
        )
    with pytest.raises(ValueError, match="foreign key"):
        adoption._inspect_database(path)


def test_backup_restore_moves_new_rows_with_the_accounts(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    for directory in (source,):
        with sqlite3.connect(directory / "scanner.db") as database:
            database.execute(_PREFERENCE, (_TIME,))
            label = f"'{directory.name}'"
            database.execute(
                _OVERRIDE.format(f"0, 'deny', NULL, NULL, NULL, {label}, NULL, NULL"), (_RULE,)
            )
            database.execute(
                _PERSONAL.format(f"'allow', NULL, NULL, NULL, {label}, NULL, NULL"),
                ("personal", 0),
            )
    chosen = source
    expected = [_rows(chosen / "scanner.db", table) for table in NEW_TABLES]
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    assert [_rows(target / "scanner.db", table) for table in NEW_TABLES] == expected
    assert expected[0][0][7] == chosen.name
