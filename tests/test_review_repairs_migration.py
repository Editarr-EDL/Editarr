"""Deleted-scan upgrades retain recoverable review data without relaxing head FKs."""

import json
import sqlite3
import subprocess
import sys

import pytest

from scanner.backup.archive import DATABASE, export_backup
from scanner.backup.restore import preview_restore
from scanner.backup.restore_target import RestoreRefused
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.preflight import prepare_database
from tests.activity_migration_support import _clone, _upgrade, link_owner
from tests.test_account_migration import _snapshot
from tests.test_review_policy_migration import _built

PREVIOUS = "0019_review_policy_evidence"


def test_export_snapshot_upgrade_preserves_0020_rows_and_recoverable_backup(tmp_path):
    path = _built(tmp_path)
    _upgrade(path, "0020_review_scan_repairs")
    original, columns = _snapshot(path)
    generation = adoption.upgrade_existing_database(path, path.parent)
    assert _snapshot(generation / "snapshot.sqlite3")[0] == original
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
        assert database.execute(
            "SELECT content_bytes, snapshot_json, destination_path, publication_revision "
            "FROM media_item_exports"
        ).fetchall() == [(None, None, None, 0)] * len(original["media_item_exports"])
    restored = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "restored.db")
    assert _snapshot(restored)[0] == original
    assert adoption._inspect_database(restored) == "0020_review_scan_repairs"
    assert adoption._inspect_database(path) == "0040_review_community_filters"


def orphan_database(tmp_path, revision=PREVIOUS):
    path = _built(tmp_path, None if revision is None else "0018_jellyfin_account_sync")
    if revision is not None:
        _upgrade(path, PREVIOUS)
    with sqlite3.connect(path) as database:
        scan_id = database.execute(
            "SELECT scan_id FROM review_scan_memberships ORDER BY review_id LIMIT 1"
        ).fetchone()[0]
        reviews = database.execute(
            "SELECT review_id FROM review_scan_memberships WHERE scan_id = ?", (scan_id,)
        ).fetchall()
        original = database.execute(
            "SELECT * FROM review_scan_memberships WHERE scan_id = ?", (scan_id,)
        ).fetchall()
        database.execute(
            "DELETE FROM media_item_export_scans WHERE media_item_export_id IN "
            "(SELECT id FROM media_item_exports WHERE review_id IS NULL)"
        )
        database.execute("DELETE FROM media_item_exports WHERE review_id IS NULL")
        database.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
    return path, scan_id, reviews, original


@pytest.mark.parametrize("interrupted", [False, True])
def test_export_snapshot_partial_upgrade_requires_verified_recovery(tmp_path, interrupted):
    from tests.test_activity_migration import _assert_recoverable

    path = _built(tmp_path)
    _upgrade(path, "0020_review_scan_repairs")
    original, _ = _snapshot(path)
    script = """
import os, sys, alembic.op
from pathlib import Path
from scanner.persistence.migrations import adoption
real = alembic.op.add_column
def fail(table, column, *args, **kwargs):
    result = real(table, column, *args, **kwargs)
    if table == "media_item_exports" and column.name == "content_bytes":
        if sys.argv[3] == "True":
            os._exit(97)
        raise RuntimeError("injected 0021 failure")
    return result
alembic.op.add_column = fail
adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(path), str(path.parent), str(interrupted)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == (97 if interrupted else 1), result.stderr.decode()
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert journal["from_revision"] == "0020_review_scan_repairs"
    assert journal["status"] == ("intent" if interrupted else "failed")
    _assert_recoverable(path, original, "0020_review_scan_repairs")


@pytest.mark.parametrize("revision", [None, PREVIOUS])
def test_orphan_upgrade_preserves_memberships_and_strict_head(tmp_path, revision):
    path, scan_id, reviews, original = orphan_database(tmp_path, revision)
    generation = adoption.upgrade_existing_database(path, path.parent)
    assert generation.is_dir()
    with sqlite3.connect(generation / "snapshot.sqlite3") as database:
        assert (
            database.execute(
                "SELECT * FROM review_scan_memberships WHERE scan_id = ?", (scan_id,)
            ).fetchall()
            == original
        )
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
        rows = database.execute(
            "SELECT review_id, missing_scan_id, preserved_json FROM review_scan_repairs"
        ).fetchall()
        assert {(row[0], row[1]) for row in rows} == {(review[0], scan_id) for review in reviews}
        assert all(json.loads(row[2])["review_scan_memberships"] for row in rows)
    assert adoption.upgrade_existing_database(path, path.parent) is None


def test_exact_removed_rows_and_blobs_are_preserved_per_review(tmp_path):
    path = _built(tmp_path)
    _upgrade(path, PREVIOUS)
    with sqlite3.connect(path) as database:
        _clone(database, "saved_reviews", "review-legacy-001", id="healthy", name="Healthy")
        _clone(database, "scans", "legacy-scan-001", id="filter-only", scan_number=99)
        database.execute(
            "INSERT INTO review_filters (review_id, id, source_scan_id, source_finding_id, "
            "position, source, start_seconds, end_seconds, edit_action, selected) "
            "VALUES ('review-legacy-001', 'manual-copy', 'filter-only', NULL, "
            "99, 'audio', 1, 2, 'mute', 1)"
        )
        database.execute(
            "INSERT INTO community_operations "
            "(operation_id, service_url, kind, state, scan_id, updated_at) "
            "VALUES ('old-receipt', 'https://community.example', 'receipt', "
            "'acknowledged', 'imported-scan-003', '2026-10-25')"
        )
        database.execute(
            "UPDATE community_imports SET artifact_bytes = ?, "
            "manifest_bytes = ?, signature_bytes = ?",
            (b"\x00artifact\xff", b"manifest", b"signature"),
        )
        database.execute(
            "INSERT INTO community_import_targets VALUES "
            "('community-import', 'media-legacy', '', '/synthetic/movie', NULL, '{}', 0)"
        )
        database.execute(
            "UPDATE media_item_exports SET review_id='review-imported-002', "
            "exported_format_revision=0 WHERE id=2"
        )
        database.execute("DELETE FROM scans WHERE id IN ('imported-scan-003', 'filter-only')")
    before, _ = _snapshot(path)
    generation = adoption.upgrade_existing_database(path, path.parent)
    assert _snapshot(generation / "snapshot.sqlite3")[0] == before
    with sqlite3.connect(path) as database:
        database.row_factory = sqlite3.Row
        repairs = database.execute(
            "SELECT * FROM review_scan_repairs ORDER BY missing_scan_id"
        ).fetchall()
        assert [(row["review_id"], row["missing_scan_id"]) for row in repairs] == [
            ("review-legacy-001", "filter-only"),
            ("review-imported-002", "imported-scan-003"),
        ]
        preserved = json.loads(repairs[1]["preserved_json"])
        import base64

        assert (
            base64.b64decode(preserved["community_imports"][0]["artifact_bytes"]["$bytes"])
            == b"\x00artifact\xff"
        )
        assert preserved["community_import_targets"][0]["import_id"] == "community-import"
        assert preserved["community_operations"][0]["operation_id"] == "old-receipt"
        assert database.execute(
            "SELECT scan_id FROM retired_community_operations WHERE operation_id = 'old-receipt'"
        ).fetchone()[0] is None
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("DELETE FROM media_item_exports WHERE id=2")
        database.execute("DELETE FROM saved_reviews WHERE id = 'review-imported-002'")
        assert database.execute("SELECT count(*) FROM review_scan_repairs").fetchone()[0] == 1
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION


def test_head_orphans_remain_fatal(tmp_path):
    path, _scan_id, _reviews, _original = orphan_database(tmp_path)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("UPDATE review_scan_memberships SET scan_id = 'new-orphan'")
    with pytest.raises(ValueError, match="child=review_scan_memberships, parent=scans"):
        adoption._inspect_database(path)


def test_export_orphan_without_review_fails_closed_and_keeps_source_and_snapshot(tmp_path):
    path = _built(tmp_path)
    _upgrade(path, PREVIOUS)
    with sqlite3.connect(path) as database:
        database.execute("DELETE FROM scans WHERE id = 'imported-scan-003'")
    original, _ = _snapshot(path)
    with pytest.raises(ValueError, match="Unattributed export scan provenance cannot be repaired"):
        adoption.upgrade_existing_database(path, path.parent)
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert journal["status"] == "failed"
    from pathlib import Path

    from scanner.backup.snapshots import validate_safety_snapshot

    generation = Path(journal["generation"])
    validate_safety_snapshot(generation)
    assert _snapshot(generation / "snapshot.sqlite3")[0] == original
    with sqlite3.connect(path) as database:
        assert database.execute(
            "SELECT scan_id FROM media_item_export_scans WHERE media_item_export_id = 2"
        ).fetchall() == [("imported-scan-003",)]
    with pytest.raises(RuntimeError, match="Previous upgrade unresolved"):
        adoption.upgrade_existing_database(path, path.parent)


@pytest.mark.parametrize("modified", [False, True])
def test_unversioned_community_era_shape_is_refused_without_writes(tmp_path, modified):
    from tests.activity_migration_support import build_activity_database

    path = tmp_path / "data" / "scanner.db"
    build_activity_database(path, tmp_path / "media", "0009_readable_api_key")
    with sqlite3.connect(path) as database:
        database.execute("DROP TABLE alembic_version")
        database.execute("DELETE FROM scans WHERE id='imported-scan-003'")
        if modified:
            database.execute("ALTER TABLE scans ADD COLUMN unknown_shape TEXT")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported unversioned"):
        adoption.upgrade_existing_database(path, path.parent)
    assert path.read_bytes() == original
    assert not (path.parent / "migration-snapshots").exists()
    assert not (path.parent / adoption.JOURNAL_NAME).exists()


def test_startup_and_legacy_backup_restore_upgrade_orphans(tmp_path, monkeypatch):
    from scanner.settings.config import settings

    path, _scan_id, _reviews, _original = orphan_database(tmp_path)
    archive = tmp_path / "orphan.backup"
    export_backup(path, path.parent, archive)
    with pytest.raises(RestoreRefused, match="restores only backups made at schema"):
        with preview_restore(archive, tmp_path / "target"):
            pass
    monkeypatch.setattr(settings, "data_dir", path.parent)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{path}")
    assert prepare_database() == adoption.HEAD_REVISION
    link_owner(path)
    second = tmp_path / "head.backup"
    export_backup(path, path.parent, second)
    with preview_restore(second, tmp_path / "head-target") as preview:
        assert (
            adoption._inspect_database(preview.stage / "database.sqlite3") == adoption.HEAD_REVISION
        )
        with sqlite3.connect(preview.stage / DATABASE) as database:
            assert database.execute("PRAGMA foreign_key_check").fetchall() == []
            assert database.execute("SELECT count(*) FROM review_scan_repairs").fetchone()[0] > 0


def test_unrelated_foreign_key_violation_remains_named_and_fatal(tmp_path):
    path, _scan_id, _reviews, _original = orphan_database(tmp_path)
    with sqlite3.connect(path) as database:
        database.execute("UPDATE saved_reviews SET owner_id = 987654321")
    with pytest.raises(ValueError, match="child=saved_reviews, parent=users"):
        adoption.upgrade_existing_database(path, path.parent)
    assert not (path.parent / "migration-snapshots").exists()


def test_accepted_credits_only_missing_scan_is_preserved(tmp_path):
    path = _built(tmp_path)
    _upgrade(path, PREVIOUS)
    with sqlite3.connect(path) as database:
        _clone(database, "scans", "legacy-scan-001", id="credits-only", scan_number=97)
        _clone(database, "scan_findings", 101, id=999, scan_id="credits-only")
        database.execute(
            "INSERT INTO review_accepted_credits (review_id, scan_id, source_finding_id, "
            "candidate_id, detected_boundary_seconds, accepted_boundary_seconds, confidence, "
            "method, accepted_at) VALUES ('review-legacy-001', 'credits-only', 999, 'candidate', "
            "90, 91, 0.9, 'visual_text', '2026-10-25')"
        )
        database.row_factory = sqlite3.Row
        original = [dict(row) for row in database.execute("SELECT * FROM review_accepted_credits")]
        database.execute("DELETE FROM scans WHERE id = 'credits-only'")
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        preserved = json.loads(
            database.execute(
                "SELECT preserved_json FROM review_scan_repairs WHERE missing_scan_id = ?",
                ("credits-only",),
            ).fetchone()[0]
        )
        assert preserved["review_accepted_credits"] == original
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_finding_parent_discovers_removed_cue_with_surviving_scan_column(tmp_path):
    path = _built(tmp_path)
    _upgrade(path, PREVIOUS)
    with sqlite3.connect(path) as database:
        _clone(database, "scans", "legacy-scan-001", id="surviving-scan", scan_number=98)
        database.execute("DELETE FROM scans WHERE id = 'legacy-scan-001'")
        database.execute(
            "UPDATE review_filters SET source_scan_id = 'surviving-scan' "
            "WHERE review_id = 'review-legacy-001'"
        )
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        preserved = json.loads(
            database.execute(
                "SELECT preserved_json FROM review_scan_repairs WHERE review_id = ?",
                ("review-legacy-001",),
            ).fetchone()[0]
        )
        assert preserved["review_filters"][0]["source_scan_id"] == "surviving-scan"
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_main_style_deleted_findings_preserve_policy_evidence(tmp_path):
    path = _built(tmp_path)
    _upgrade(path, PREVIOUS)
    scan_id = "legacy-scan-001"
    with sqlite3.connect(path) as database:
        database.row_factory = sqlite3.Row
        original = [
            dict(row)
            for row in database.execute(
                "SELECT e.* FROM review_policy_evidence e JOIN scan_findings f "
                "ON f.id = e.source_finding_id WHERE f.scan_id = ?",
                (scan_id,),
            )
        ]
        assert original
        database.execute("DELETE FROM scan_findings WHERE scan_id = ?", (scan_id,))
        database.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        preserved = [
            value
            for (payload,) in database.execute(
                "SELECT preserved_json FROM review_scan_repairs WHERE missing_scan_id = ?",
                (scan_id,),
            )
            for value in json.loads(payload)["review_policy_evidence"]
        ]
        assert preserved == original
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_0020_keeps_verified_snapshot_and_refuses_partial_admission(tmp_path, monkeypatch):
    from sqlalchemy.engine import Connection

    from tests.test_activity_migration import _assert_recoverable

    path, *_ = orphan_database(tmp_path)
    original, _ = _snapshot(path)
    real_execute = Connection.execute

    def fail(connection, statement, *args, **kwargs):
        if "INSERT INTO review_scan_repairs" in str(statement):
            raise RuntimeError("injected 0020 failure")
        return real_execute(connection, statement, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", fail)
    with pytest.raises(RuntimeError, match="injected 0020 failure"):
        adoption.upgrade_existing_database(path, path.parent)
    monkeypatch.setattr(Connection, "execute", real_execute)
    assert json.loads((path.parent / adoption.JOURNAL_NAME).read_text())["status"] == "failed"
    _assert_recoverable(path, original, PREVIOUS)


def test_interrupted_0020_keeps_recoverable_source(tmp_path):
    from tests.test_activity_migration import _assert_recoverable

    path, *_ = orphan_database(tmp_path)
    original, _ = _snapshot(path)
    script = (
        "import os, sys; from pathlib import Path; from sqlalchemy.engine import Connection; "
        "from scanner.persistence.migrations import adoption; real = Connection.execute; "
        "Connection.execute = lambda connection, statement, *args, **kwargs: os._exit(96) "
        "if 'INSERT INTO review_scan_repairs' in str(statement) "
        "else real(connection, statement, *args, **kwargs); "
        "adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(path), str(path.parent)], check=False
    )
    assert result.returncode == 96
    assert json.loads((path.parent / adoption.JOURNAL_NAME).read_text())["status"] == "intent"
    _assert_recoverable(path, original, PREVIOUS)


def test_upgraded_activation_and_head_backup_round_trip_preserve_repair_records(tmp_path):
    from scanner.backup.activation import activate_restore
    from tests.test_backup_activation import _fixture

    source = tmp_path / "legacy"
    source.mkdir()
    path, *_ = orphan_database(source)
    adoption.upgrade_existing_database(path, path.parent)
    link_owner(path)
    archive = tmp_path / "legacy.backup"
    export_backup(path, path.parent, archive)
    activation = tmp_path / "activation"
    activation.mkdir()
    _unused, target = _fixture(activation)
    with preview_restore(archive, target) as preview:
        with sqlite3.connect(preview.stage / DATABASE) as database:
            expected = database.execute("SELECT * FROM review_scan_repairs ORDER BY id").fetchall()
            assert expected
        activate_restore(preview, target / "scanner.db", target, confirmed=True)
    assert adoption._inspect_database(target / "scanner.db") == adoption.HEAD_REVISION
    with sqlite3.connect(target / "scanner.db") as database:
        assert (
            database.execute("SELECT * FROM review_scan_repairs ORDER BY id").fetchall() == expected
        )
    head_archive = tmp_path / "head-round-trip.backup"
    export_backup(target / "scanner.db", target, head_archive)
    with preview_restore(head_archive, tmp_path / "head-target") as preview:
        with sqlite3.connect(preview.stage / DATABASE) as database:
            assert (
                database.execute("SELECT * FROM review_scan_repairs ORDER BY id").fetchall()
                == expected
            )
            assert database.execute("PRAGMA foreign_key_check").fetchall() == []
