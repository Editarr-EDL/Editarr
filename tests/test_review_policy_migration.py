"""Revision 0019 retains source bindings with backup-first, recoverable upgrades."""

import json
import sqlite3
import subprocess
import sys

import pytest

from scanner.persistence.migrations import adoption
from tests.activity_migration_support import _upgrade, link_owner
from tests.community_identity_migration_support import no_network as no_network
from tests.community_master_migration_support import retire_publications
from tests.explicit_filter_modes_support import drop_filter_migration_additions
from tests.stable_media_identity_migration_support import without_stable_media_identity
from tests.test_account_migration import _snapshot
from tests.test_activity_migration import _assert_recoverable
from tests.test_jellyfin_sync_migration import PROFILES
from tests.test_jellyfin_sync_migration import SOURCES as SYNC_SOURCES
from tests.test_jellyfin_sync_migration import _built as _sync_built

PREVIOUS = "0018_jellyfin_account_sync"
SOURCES = [*SYNC_SOURCES, PREVIOUS]


def _built(tmp_path, revision=PREVIOUS):
    path = _sync_built(tmp_path, PROFILES if revision == PREVIOUS else revision)
    if revision == PREVIOUS:
        _upgrade(path, PREVIOUS)
    return path


@pytest.mark.parametrize("revision", SOURCES)
def test_every_supported_revision_retains_rows_and_verified_recovery(tmp_path, revision):
    path = _built(tmp_path, revision)
    original, _ = _snapshot(path)
    generation = adoption.upgrade_existing_database(path, path.parent)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    upgraded, _ = _snapshot(path)
    drop_filter_migration_additions(upgraded, original)
    evidence = upgraded.pop("review_policy_evidence")
    assert upgraded.pop("review_scan_repairs") == []
    assert upgraded.pop("media_item_export_regenerations") == []
    assert upgraded.pop("account_link_transitions") == []
    assert upgraded.pop("account_link_export_moves") == []
    assert upgraded.pop("account_dispositions") == []
    assert upgraded.pop("account_transfer_moves") == []
    assert upgraded.pop("jellyfin_user_blocks") == []
    assert evidence
    if revision == PREVIOUS:
        assert all(
            row[-5:] == (None, None, None, 0, None) for row in upgraded["media_item_exports"]
        )
        upgraded["media_item_exports"] = [row[:-5] for row in upgraded["media_item_exports"]]
        assert without_stable_media_identity(upgraded) == retire_publications(original, upgraded)
    assert adoption.upgrade_existing_database(path, path.parent) is None
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert generation.is_dir()
    assert adoption._inspect_database(recovered) == revision
    assert _snapshot(recovered)[0] == original


def test_backfill_uses_immutable_source_not_edited_filter_interval(tmp_path):
    path = _built(tmp_path)
    with sqlite3.connect(path) as database:
        database.execute("UPDATE review_filters SET start_seconds = 20, end_seconds = 21")
        expected = database.execute(
            "SELECT m.review_id, f.id FROM review_scan_memberships m "
            "JOIN scan_findings f ON f.scan_id = m.scan_id "
            "WHERE m.role = 'both' OR m.role = CASE f.source "
            "WHEN 'audio' THEN 'audio' ELSE 'video' END "
            "ORDER BY 1, 2"
        ).fetchall()
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        bindings = database.execute(
            "SELECT review_id, source_finding_id FROM review_policy_evidence ORDER BY 1, 2"
        ).fetchall()
        assert bindings == expected
        assert (
            database.execute(
                "SELECT count(*) FROM review_policy_evidence WHERE start_seconds = 20"
            ).fetchone()[0]
            == 0
        )
        assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_backfill_float_order_is_covered_by_import_mapping(tmp_path):
    from types import SimpleNamespace

    from scanner.mcf import TimingInterval, map_affine_range
    from scanner.models import ReviewFilter
    from scanner.persistence.repository.review_policy import _map_policy_floor
    from scanner.persistence.repository.review_policy_evidence import covers_evidence, snap_evidence

    path = _built(tmp_path)
    with sqlite3.connect(path) as database:
        scan_id = database.execute(
            "SELECT m.scan_id FROM review_scan_memberships m "
            "JOIN scan_findings f ON f.scan_id = m.scan_id "
            "WHERE m.role IN ('both', 'audio') AND f.source = 'audio' LIMIT 1"
        ).fetchone()[0]
        database.execute(
            "UPDATE scans SET duration_seconds = 100, video_hash = NULL WHERE id = ?",
            (scan_id,),
        )
        database.execute(
            "UPDATE scan_mcf_documents SET material_start_seconds = 0, "
            "material_end_seconds = 100 WHERE scan_id = ?",
            (scan_id,),
        )
        database.execute(
            "UPDATE review_synchronizations SET start_seconds = 5, end_seconds = 120 "
            "WHERE review_id IN (SELECT review_id FROM review_scan_memberships WHERE scan_id = ?)",
            (scan_id,),
        )
        database.executemany(
            "INSERT INTO scan_findings (scan_id, source, start_seconds, end_seconds, "
            "edit_action, selected, created_at, label) "
            "VALUES (?, 'audio', ?, ?, 'mute', 0, CURRENT_TIMESTAMP, 'affine-order-probe')",
            [(scan_id, index / 21, index / 21 + 0.7) for index in range(1, 2000)],
        )
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        rows = database.execute(
            "SELECT f.start_seconds, f.end_seconds, e.start_seconds, e.end_seconds "
            "FROM scan_findings f JOIN review_policy_evidence e ON e.source_finding_id = f.id "
            "WHERE f.label = 'affine-order-probe'"
        ).fetchall()
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    assert len(rows) >= 1999
    for start, end, mapped_start, mapped_end in rows:
        assert (mapped_start, mapped_end) == (start, end)
        synchronization = SimpleNamespace(start_seconds=5.0, end_seconds=120.0)
        legacy = map_affine_range(start, end, TimingInterval(0, 100), TimingInterval(5, 120))
        floor = _map_policy_floor(start, end, TimingInterval(0, 100), TimingInterval(5, 120))
        required = ReviewFilter(
            id="legacy",
            source_scan_id=scan_id,
            source_finding_id=1,
            source="audio",
            label="Legacy",
            start_seconds=floor[0],
            end_seconds=floor[1],
            edit_action="mute",
            category=None,
            subcategory=None,
            details=None,
            group_key=None,
            confidence=None,
            selected=True,
        )
        imported = required.model_copy(
            update={"start_seconds": legacy[0], "end_seconds": legacy[1]}
        )
        assert covers_evidence(snap_evidence(imported, required, synchronization), required)
    with sqlite3.connect(path) as database:
        assert database.execute(
            "SELECT DISTINCT e.sync_start, e.sync_end FROM review_policy_evidence e "
            "JOIN scan_findings f ON e.source_finding_id = f.id "
            "WHERE f.label = 'affine-order-probe'"
        ).fetchall() == [(0.0, 100.0)]


def test_failure_after_table_creation_is_refused_and_recovers(tmp_path, monkeypatch):
    import alembic.op

    path = _built(tmp_path)
    original, _ = _snapshot(path)
    real_execute = alembic.op.execute

    def fail(statement, *args, **kwargs):
        if "INSERT INTO review_policy_evidence" in str(statement):
            raise RuntimeError("injected 0019 failure")
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(alembic.op, "execute", fail)
    with pytest.raises(RuntimeError, match="injected 0019 failure"):
        adoption.upgrade_existing_database(path, path.parent)
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert (journal["status"], journal["from_revision"]) == ("failed", PREVIOUS)
    monkeypatch.setattr(alembic.op, "execute", real_execute)
    _assert_recoverable(path, original, PREVIOUS)


def test_interrupted_backfill_is_refused_and_recovers(tmp_path):
    path = _built(tmp_path)
    original, _ = _snapshot(path)
    script = (
        "import os, sys, alembic.op; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        "real = alembic.op.execute; "
        "alembic.op.execute = lambda statement, *args, **kwargs: os._exit(96) "
        "if 'INSERT INTO review_policy_evidence' in str(statement) "
        "else real(statement, *args, **kwargs); "
        "adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(path), str(path.parent)], check=False
    )
    assert result.returncode == 96
    assert json.loads((path.parent / adoption.JOURNAL_NAME).read_text())["status"] == "intent"
    _assert_recoverable(path, original, PREVIOUS)


@pytest.mark.parametrize(
    "mutation",
    [
        "CREATE TABLE review_policy_evidence (review_id TEXT)",
        "UPDATE alembic_version SET version_num = '0020_unknown'",
    ],
)
def test_modified_or_unknown_source_is_not_stamped_or_snapshotted(tmp_path, mutation):
    path = _built(tmp_path)
    with sqlite3.connect(path) as database:
        database.execute(mutation)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported"):
        adoption.upgrade_existing_database(path, path.parent)
    assert path.read_bytes() == before
    assert not (path.parent / adoption.JOURNAL_NAME).exists()
    assert not (path.parent / "migration-snapshots").exists()


def test_modified_head_and_invalid_evidence_are_refused(tmp_path):
    path = _built(tmp_path)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA foreign_keys = ON")
        for statement in (
            "UPDATE review_policy_evidence SET start_seconds = -1",
            "UPDATE review_policy_evidence SET end_seconds = start_seconds",
            "UPDATE review_policy_evidence SET sync_end = sync_start",
            "UPDATE review_policy_evidence SET retime = 2",
            "UPDATE review_policy_evidence SET source_finding_id = -1",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(statement)
        database.execute("ALTER TABLE review_policy_evidence ADD COLUMN unknown TEXT")
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption._inspect_database(path)


def test_backup_restore_retains_the_policy_evidence(tmp_path):
    from scanner.backup.activation import activate_restore
    from scanner.backup.archive import export_backup
    from scanner.backup.restore import preview_restore
    from scanner.persistence.migrations.runner import migrate_fresh_database

    source = _built(tmp_path)
    adoption.upgrade_existing_database(source, source.parent)
    link_owner(source)
    with sqlite3.connect(source) as database:
        expected = database.execute("SELECT * FROM review_policy_evidence ORDER BY 1, 2").fetchall()
    assert expected
    target = tmp_path / "target"
    target.mkdir()
    migrate_fresh_database(target / "scanner.db", target)
    archive = tmp_path / "full.backup"
    export_backup(source, source.parent, archive)
    with preview_restore(archive, target) as preview:
        activate_restore(preview, target / "scanner.db", target, confirmed=True)
    with sqlite3.connect(target / "scanner.db") as database:
        assert (
            database.execute("SELECT * FROM review_policy_evidence ORDER BY 1, 2").fetchall()
            == expected
        )
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
