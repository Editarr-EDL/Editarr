"""MU-11: revision 0014 adds owner-granted media exceptions without changing any row.

Covers every supported source revision, a failed and an interrupted upgrade, a
partly applied shape, an unknown revision, and recovery from the verified
pre-upgrade generation. Revision 0026 later drops the table, so the head has none.
Sources come from `mu06-community-fixture-1`, upgraded to 0013 for the newest one.
"""

import json
import sqlite3
import subprocess
import sys

import pytest

from scanner.persistence.migrations import adoption
from tests.activity_migration_support import _upgrade, rebind_to_jellyfin
from tests.community_identity_migration_support import (
    PERSONAL_FILTERS,
    SOURCES,
    build_community_database,
)
from tests.community_identity_migration_support import no_network as no_network
from tests.community_master_migration_support import retire_publications
from tests.explicit_filter_modes_support import drop_0031_additions
from tests.jellyfin_sync_migration_support import drop_0018_additions
from tests.stable_media_identity_migration_support import without_stable_media_identity
from tests.test_account_migration import _snapshot
from tests.test_activity_migration import _assert_recoverable

IDENTITIES = "0013_community_identities"
MIGRATION_SOURCES = [*SOURCES, IDENTITIES]
_TIME = "2026-10-22 12:00:00.000000"


def _built(tmp_path, revision):
    """A populated database at `revision`, with a local media override from 0011 on."""
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700, parents=True)
    build_community_database(
        path, tmp_path / "media", PERSONAL_FILTERS if revision == IDENTITIES else revision
    )
    if revision == IDENTITIES:
        _upgrade(path, IDENTITIES)
    rebind_to_jellyfin(path)
    with sqlite3.connect(path) as database:
        if database.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'media_access_scopes'"
        ).fetchone():
            database.execute("UPDATE media_access_scopes SET all_sources = 0 WHERE id = 1")
            database.execute("INSERT INTO media_access_scope_sources VALUES (1, 'radarr', '')")
    return path


@pytest.mark.parametrize("revision", MIGRATION_SOURCES)
def test_every_supported_revision_upgrades_with_every_row_kept(tmp_path, revision):
    """Earlier revisions change rows as their own tests describe; 0014 itself changes none."""
    path = _built(tmp_path, revision)
    original, _ = _snapshot(path)

    generation = adoption.upgrade_existing_database(path, path.parent)

    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    upgraded, _ = _snapshot(path)
    assert upgraded.pop("media_access_exceptions") == []
    # Revision 0015 appends an empty `secret_envelope` to each key row.
    assert [row[-1] for row in upgraded["api_keys"]] == [None] * len(upgraded["api_keys"])
    upgraded["api_keys"] = [row[:-1] for row in upgraded["api_keys"]]
    # Revision 0016 adds two empty personal filter rule tables.
    assert upgraded.pop("user_filter_rule_overrides") == []
    assert upgraded.pop("user_filter_personal_rules") == []
    # Revision 0017 adds three empty account profile tables.
    for table in ("user_profiles", "user_avatars", "account_settings"):
        assert upgraded.pop(table) == []
    drop_0018_additions(upgraded)
    drop_0031_additions(upgraded, original)
    if revision == IDENTITIES:
        assert without_stable_media_identity(upgraded) == retire_publications(original, upgraded)
    assert adoption.upgrade_existing_database(path, path.parent) is None
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert generation.is_dir()
    assert adoption._inspect_database(recovered) == revision
    assert _snapshot(recovered)[0] == original


def test_failure_inside_0014_blocks_admission_and_recovers(tmp_path, monkeypatch):
    import alembic.op

    path = _built(tmp_path, IDENTITIES)
    original, _ = _snapshot(path)
    real_create_table = alembic.op.create_table

    def fail(name, *args, **kwargs):
        if name == "media_access_exceptions":
            raise RuntimeError("injected 0014 failure")
        return real_create_table(name, *args, **kwargs)

    monkeypatch.setattr(alembic.op, "create_table", fail)
    with pytest.raises(RuntimeError, match="injected 0014 failure"):
        adoption.upgrade_existing_database(path, path.parent)
    monkeypatch.setattr(alembic.op, "create_table", real_create_table)
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert (journal["status"], journal["from_revision"]) == ("failed", IDENTITIES)
    _assert_recoverable(path, original, IDENTITIES)


def test_hard_exit_inside_0014_blocks_admission_and_recovers(tmp_path):
    path = _built(tmp_path, IDENTITIES)
    original, _ = _snapshot(path)
    script = (
        "import os, sys, alembic.op; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        "real = alembic.op.create_table; "
        "alembic.op.create_table = lambda name, *a, **k: os._exit(94) "
        "if name == 'media_access_exceptions' else real(name, *a, **k); "
        "adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(path.parent)], check=False
    )
    assert result.returncode == 94
    assert json.loads((path.parent / adoption.JOURNAL_NAME).read_text())["status"] == "intent"
    _assert_recoverable(path, original, IDENTITIES)


def test_a_partly_applied_0014_shape_is_refused_without_a_snapshot(tmp_path):
    path = _built(tmp_path, IDENTITIES)
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE media_access_exceptions (user_id INTEGER PRIMARY KEY)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption.upgrade_existing_database(path, path.parent)
    assert path.read_bytes() == before
    assert not (path.parent / adoption.JOURNAL_NAME).exists()
    assert not (path.parent / "migration-snapshots").exists()


def test_an_unknown_revision_is_refused_without_a_snapshot(tmp_path):
    path = _built(tmp_path, IDENTITIES)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("UPDATE alembic_version SET version_num = '0015_future_revision'")
    snapshots = sorted((path.parent / "migration-snapshots").iterdir())
    with pytest.raises(ValueError, match="Unsupported database revision"):
        adoption._inspect_database(path)
    with pytest.raises(ValueError, match="Unsupported database revision"):
        adoption.upgrade_existing_database(path, path.parent)
    assert sorted((path.parent / "migration-snapshots").iterdir()) == snapshots
