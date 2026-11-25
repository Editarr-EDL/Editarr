"""MU-16: revision 0018 adds Jellyfin admission and account sync state without changing rows.

Covers every supported source revision, a failed and an interrupted upgrade, a
partly applied or modified shape, an unknown revision, recovery from the verified
pre-upgrade generation, the new checks, and backups that move the sync state and
admission mode with the local accounts.
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
from tests.test_backup_activation import _fixture, _restore
from tests.test_user_profiles_migration import SOURCES as PROFILE_SOURCES
from tests.test_user_profiles_migration import _built as _profiles_built

PROFILES = "0017_user_profiles"
SOURCES = [*PROFILE_SOURCES, PROFILES]
_TIME = "2026-10-23 12:00:00.000000"
_POLICY = '{"all_libraries":true}'
_SYNC = (
    "INSERT INTO jellyfin_account_sync VALUES "
    "(1, ?, 'Owner', NULL, 1, ?, ?, ?, 'verified', NULL, ?)"
)


def _built(tmp_path, revision):
    path = _profiles_built(tmp_path, PROFILE_SOURCES[-1] if revision == PROFILES else revision)
    if revision == PROFILES:
        _upgrade(path, PROFILES)
        with sqlite3.connect(path) as database:
            database.execute("INSERT INTO account_settings VALUES (1, 'open', ?)", (_TIME,))
    return path


def _rows(path):
    with sqlite3.connect(path) as database:
        return {
            "sync": database.execute("SELECT * FROM jellyfin_account_sync").fetchall(),
            "settings": database.execute("SELECT * FROM account_settings").fetchall(),
        }


@pytest.mark.parametrize("revision", SOURCES)
def test_every_supported_revision_upgrades_with_every_row_kept(tmp_path, revision):
    path = _built(tmp_path, revision)
    original, _ = _snapshot(path)

    generation = adoption.upgrade_existing_database(path, path.parent)

    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert adoption.HEAD_REVISION == "0038_retire_receipt_capabilities"
    upgraded, _ = _snapshot(path)
    drop_0018_additions(upgraded)
    drop_0031_additions(upgraded, original)
    if revision == PROFILES:
        expected = dict(original)
        settings = upgraded.pop("account_settings")
        # The existing row has numbered no Jellyfin read yet in any database epoch.
        assert [row[:2] for row in settings] == expected.pop("account_settings")
        assert [tuple(row[2:]) for row in settings] == [(0, None, 0, None)]
        assert without_stable_media_identity(upgraded) == retire_publications(expected, upgraded)
    assert adoption.upgrade_existing_database(path, path.parent) is None
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert generation.is_dir()
    assert adoption._inspect_database(recovered) == revision
    assert _snapshot(recovered)[0] == original


def test_new_checks_refuse_values_the_application_never_writes(tmp_path):
    path = _built(tmp_path, PROFILES)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA foreign_keys = ON")
        database.execute(_SYNC, ("enabled", _POLICY, _TIME, _TIME, _TIME))
        for statement in (
            "UPDATE account_settings SET jellyfin_read_generation = -1",
            "UPDATE account_settings SET jellyfin_clock_fence = -1",
            "UPDATE account_settings SET jellyfin_clock_fence = jellyfin_read_generation + 1",
            "UPDATE account_settings SET jellyfin_read_epoch = ''",
            "UPDATE account_settings SET jellyfin_read_epoch = 'not-a-database-read-epoch'",
            f"UPDATE account_settings SET jellyfin_read_epoch = '{'z' * 32}'",
            "UPDATE jellyfin_account_sync SET upstream_state = 'gone'",
            "UPDATE jellyfin_account_sync SET jellyfin_name = ''",
            "UPDATE jellyfin_account_sync SET avatar_tag = ''",
            "UPDATE jellyfin_account_sync SET is_administrator = 2",
            "UPDATE jellyfin_account_sync SET media_policy = '{'",
            "UPDATE jellyfin_account_sync SET last_outcome = 'pending'",
            "UPDATE jellyfin_account_sync SET last_failure = 'timeout'",
            "UPDATE jellyfin_account_sync SET last_outcome = 'unverified'",
            "UPDATE jellyfin_account_sync SET user_id = 999",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(statement)
        database.execute(
            "UPDATE jellyfin_account_sync SET last_outcome = 'unverified', last_failure = 'timeout'"
        )


def test_existing_disabled_accounts_keep_a_local_disable(tmp_path):
    path = _built(tmp_path, PROFILES)
    with sqlite3.connect(path) as database:
        for status, export in (("disabled", "a"), ("active", "b")):
            database.execute(
                "INSERT INTO users (role, status, local_credential, local_export_id, "
                "permission_revision, created_at, updated_at) "
                "VALUES ('user', ?, 'none', ?, 1, ?, ?)",
                (status, "local-" + export * 32, _TIME, _TIME),
            )
        disabled = database.execute("SELECT count(*) FROM users WHERE status = 'disabled'")
        disabled = disabled.fetchone()[0]
    assert disabled >= 1

    adoption.upgrade_existing_database(path, path.parent)

    with sqlite3.connect(path) as database:
        causes = database.execute(
            "SELECT status, disable_cause FROM users GROUP BY 1, 2 ORDER BY 1"
        ).fetchall()
        # 0030 then disables every account without a Jellyfin link, the owner included.
        assert causes == [("disabled", "local"), ("disabled", "unlinked")]
        for statement in (
            "UPDATE users SET disable_cause = 'admin' WHERE status = 'disabled'",
            "UPDATE users SET status = 'active' WHERE disable_cause = 'unlinked'",
            "UPDATE users SET status = 'active' WHERE status = 'disabled'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(statement)
        database.execute(
            "UPDATE users SET disable_cause = 'upstream_deleted' "
            "WHERE status = 'disabled' AND role != 'owner'"
        )


def test_failure_inside_0018_blocks_admission_and_recovers(tmp_path, monkeypatch):
    import alembic.op

    path = _built(tmp_path, PROFILES)
    original, _ = _snapshot(path)
    real_create_table = alembic.op.create_table

    def fail(name, *args, **kwargs):
        if name == "jellyfin_account_sync":
            raise RuntimeError("injected 0018 failure")
        return real_create_table(name, *args, **kwargs)

    monkeypatch.setattr(alembic.op, "create_table", fail)
    with pytest.raises(RuntimeError, match="injected 0018 failure"):
        adoption.upgrade_existing_database(path, path.parent)
    monkeypatch.setattr(alembic.op, "create_table", real_create_table)
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert (journal["status"], journal["from_revision"]) == ("failed", PROFILES)
    _assert_recoverable(path, original, PROFILES)


def test_hard_exit_inside_0018_blocks_admission_and_recovers(tmp_path):
    path = _built(tmp_path, PROFILES)
    original, _ = _snapshot(path)
    script = (
        "import os, sys, alembic.op; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        "real = alembic.op.create_table; "
        "alembic.op.create_table = lambda name, *a, **k: os._exit(95) "
        "if name == 'jellyfin_account_sync' else real(name, *a, **k); "
        "adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(path.parent)], check=False
    )
    assert result.returncode == 95
    assert json.loads((path.parent / adoption.JOURNAL_NAME).read_text())["status"] == "intent"
    _assert_recoverable(path, original, PROFILES)


def test_a_partly_applied_0018_shape_is_refused_without_a_snapshot(tmp_path):
    path = _built(tmp_path, PROFILES)
    with sqlite3.connect(path) as database:
        database.execute(
            "ALTER TABLE account_settings ADD COLUMN jellyfin_admission VARCHAR "
            "DEFAULT 'first_login' NOT NULL"
        )
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption.upgrade_existing_database(path, path.parent)
    assert path.read_bytes() == before
    assert not (path.parent / adoption.JOURNAL_NAME).exists()
    assert not (path.parent / "migration-snapshots").exists()


def test_a_modified_0018_head_is_refused(tmp_path):
    path = _built(tmp_path, PROFILES)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("DROP TABLE jellyfin_account_sync")
        database.execute("CREATE TABLE jellyfin_account_sync (user_id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption._inspect_database(path)


def test_an_unmerged_0018_without_the_clock_fence_is_refused_without_mutating_it(tmp_path):
    path = _built(tmp_path, PROFILES)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("ALTER TABLE account_settings DROP COLUMN jellyfin_clock_fence")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption.upgrade_existing_database(path, path.parent)
    assert path.read_bytes() == before


def test_an_unmerged_0018_without_the_read_epoch_is_refused_without_mutating_it(tmp_path):
    path = _built(tmp_path, PROFILES)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("ALTER TABLE account_settings DROP COLUMN jellyfin_read_epoch")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption.upgrade_existing_database(path, path.parent)
    assert path.read_bytes() == before


def test_an_unknown_revision_is_refused_without_a_snapshot(tmp_path):
    path = _built(tmp_path, PROFILES)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("UPDATE alembic_version SET version_num = '0019_future_revision'")
    snapshots = sorted((path.parent / "migration-snapshots").iterdir())
    with pytest.raises(ValueError, match="Unsupported database revision"):
        adoption._inspect_database(path)
    with pytest.raises(ValueError, match="Unsupported database revision"):
        adoption.upgrade_existing_database(path, path.parent)
    assert sorted((path.parent / "migration-snapshots").iterdir()) == snapshots


def test_backup_restore_moves_sync_state_with_the_accounts(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    for directory, state, generation in ((source, "disabled", 7),):
        with sqlite3.connect(directory / "scanner.db") as database:
            database.execute("DELETE FROM account_settings")
            database.execute(
                "INSERT INTO account_settings VALUES (1, ?, ?, ?, 5, ?)",
                (_TIME, generation, _TIME, "a" * 32),
            )
            database.execute(_SYNC, (state, _POLICY, _TIME, _TIME, _TIME))
    expected = _rows(source / "scanner.db")
    # The read counter and clock fence travel with the accounts they order; every
    # restore clears the read epoch, so no read begun before it applies after it.
    expected["settings"] = [(*row[:-1], None) for row in expected["settings"]]
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    assert _rows(target / "scanner.db") == expected
    assert expected["sync"][0][1] == "disabled"
