"""Safety snapshots are exercised only against disposable SQLite databases."""

import errno
import json
import os
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from scanner.backup import snapshots
from scanner.backup.snapshots import (
    SnapshotError,
    cleanup_incomplete_snapshots,
    create_safety_snapshot,
    select_recovery_snapshot,
    validate_safety_snapshot,
)
from scanner.runtime.maintenance import DatabaseMaintenance, MaintenanceUnavailable
from tests.community_schema_fixture import create_unversioned_fixture
from tests.community_schema_support import baseline_specification


def test_snapshot_includes_committed_wal_rows(tmp_path):
    database_path = tmp_path / "active.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE specimen (value TEXT NOT NULL)")
        connection.execute("INSERT INTO specimen VALUES ('before')")
    writer = sqlite3.connect(database_path)
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO specimen VALUES ('in-wal')")
        writer.commit()
        snapshot = create_safety_snapshot(database_path, tmp_path)
        assert select_recovery_snapshot(tmp_path) == snapshot
        manifest = validate_safety_snapshot(snapshot)
        assert manifest["generation"] == snapshot.name.removeprefix("snapshot-")
        with sqlite3.connect(snapshot / "snapshot.sqlite3") as recovered:
            assert recovered.execute("SELECT value FROM specimen ORDER BY value").fetchall() == [
                ("before",),
                ("in-wal",),
            ]
        assert writer.execute("SELECT count(*) FROM specimen").fetchone() == (2,)
    finally:
        writer.close()


def test_online_backup_omits_uncommitted_writer_and_keeps_active_database(tmp_path):
    database_path = tmp_path / "active.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE specimen (value TEXT)")
        connection.execute("INSERT INTO specimen VALUES ('committed')")
    writer = sqlite3.connect(database_path)
    try:
        writer.execute("INSERT INTO specimen VALUES ('uncommitted')")
        snapshot = create_safety_snapshot(database_path, tmp_path)
        with sqlite3.connect(snapshot / "snapshot.sqlite3") as recovered:
            assert recovered.execute("SELECT value FROM specimen").fetchall() == [
                ("committed",)
            ]
        writer.rollback()
        assert writer.execute("SELECT value FROM specimen").fetchall() == [("committed",)]
    finally:
        writer.close()


def test_full_unversioned_fixture_round_trips_independently(tmp_path):
    database_path = tmp_path / "legacy.db"
    source = create_unversioned_fixture(database_path)
    source.dispose()
    snapshot = create_safety_snapshot(database_path, tmp_path)
    recovered = create_engine(f"sqlite:///{snapshot / 'snapshot.sqlite3'}")
    try:
        expected = json.loads(
            (Path(__file__).parent / "fixtures/community/unversioned-v1.json").read_text(
                encoding="utf-8"
            )
        )
        source_engine = create_engine(f"sqlite:///{database_path}")
        try:
            assert baseline_specification(recovered) == expected
            assert baseline_specification(source_engine) == expected
        finally:
            source_engine.dispose()
        with recovered.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT id, scan_number FROM scans"
            ).one() == ("legacy-scan-001", 7)
            assert connection.exec_driver_sql(
                "SELECT id, selected FROM review_filters"
            ).one() == ("review-filter-001", 0)
    finally:
        recovered.dispose()


def test_snapshot_can_be_created_inside_existing_exclusive_lease(tmp_path):
    database_path = tmp_path / "active.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE specimen (value TEXT)")
    maintenance = DatabaseMaintenance(tmp_path)
    with maintenance.exclusive():
        snapshot = create_safety_snapshot(database_path, tmp_path)
        assert validate_safety_snapshot(snapshot)["format_version"] == 1
        assert maintenance.owns_exclusive()
    assert select_recovery_snapshot(tmp_path) == snapshot


def test_snapshot_timeout_with_active_worker_leaves_source_unchanged(tmp_path):
    database_path = tmp_path / "active.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE specimen (value TEXT)")
        connection.execute("INSERT INTO specimen VALUES ('original')")
    with DatabaseMaintenance(tmp_path).worker_lease():
        with pytest.raises(MaintenanceUnavailable, match="Timed out"):
            create_safety_snapshot(database_path, tmp_path, timeout=0.1)
    assert select_recovery_snapshot(tmp_path) is None
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT value FROM specimen").fetchone() == ("original",)


@pytest.mark.parametrize("invalid_source", ["missing", "corrupt", "foreign_key"])
def test_invalid_source_never_publishes_a_generation(tmp_path, invalid_source):
    database_path = tmp_path / "active.db"
    if invalid_source == "corrupt":
        database_path.write_bytes(b"not a SQLite database")
    elif invalid_source == "foreign_key":
        with sqlite3.connect(database_path) as connection:
            connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            connection.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
            connection.execute("INSERT INTO child VALUES (1)")
    with pytest.raises((SnapshotError, sqlite3.DatabaseError)):
        create_safety_snapshot(database_path, tmp_path)
    assert select_recovery_snapshot(tmp_path) is None
    root = tmp_path / "migration-snapshots"
    assert not root.exists() or list(root.iterdir()) == []


@pytest.mark.parametrize("failure", ["disk_full", "interrupted", "after_publish"])
def test_failed_generation_never_changes_active_or_advertises_backup(
    tmp_path, monkeypatch, failure
):
    database_path = tmp_path / "active.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE specimen (value TEXT)")
        connection.execute("INSERT INTO specimen VALUES ('original')")
    if failure == "disk_full":
        monkeypatch.setattr(
            snapshots, "_sync_directory",
            lambda _path: (_ for _ in ()).throw(OSError(errno.ENOSPC, "disk full")),
        )
        error_type = OSError
    elif failure == "interrupted":
        monkeypatch.setattr(
            snapshots, "_checksum", lambda _path: (_ for _ in ()).throw(KeyboardInterrupt())
        )
        error_type = KeyboardInterrupt
    else:
        original = snapshots._sync_directory

        def fail_after_publish(path):
            if path.name == "migration-snapshots":
                raise OSError(errno.ENOSPC, "disk full")
            original(path)

        monkeypatch.setattr(snapshots, "_sync_directory", fail_after_publish)
        error_type = OSError
    with pytest.raises(error_type):
        create_safety_snapshot(database_path, tmp_path)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT value FROM specimen").fetchone() == ("original",)
    assert select_recovery_snapshot(tmp_path) is None
    assert list((tmp_path / "migration-snapshots").iterdir()) == []


def test_corrupt_published_generation_is_not_selected(tmp_path):
    database_path = tmp_path / "active.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE specimen (value TEXT)")
    older = create_safety_snapshot(database_path, tmp_path)
    newest = create_safety_snapshot(database_path, tmp_path)
    with (newest / "snapshot.sqlite3").open("ab") as database_copy:
        database_copy.write(b"tampered")
    with pytest.raises(SnapshotError, match="does not match"):
        select_recovery_snapshot(tmp_path)
    assert validate_safety_snapshot(older)["format_version"] == 1


def test_cleanup_removes_only_incomplete_generation(tmp_path):
    database_path = tmp_path / "active.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE specimen (value TEXT)")
    published = create_safety_snapshot(database_path, tmp_path)
    root = tmp_path / "migration-snapshots"
    incomplete = root / ".pending-abandoned"
    incomplete.mkdir(mode=0o700)
    (incomplete / "partial").write_bytes(b"incomplete")
    unrelated = root / "operator-note"
    unrelated.write_bytes(b"keep")
    assert cleanup_incomplete_snapshots(tmp_path) == 1
    assert not incomplete.exists()
    assert unrelated.read_bytes() == b"keep"
    assert select_recovery_snapshot(tmp_path) == published
    assert cleanup_incomplete_snapshots(tmp_path) == 0


def test_published_files_are_owner_only(tmp_path):
    database_path = tmp_path / "active.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE specimen (value TEXT)")
    published = create_safety_snapshot(database_path, tmp_path)
    if os.name == "posix":
        paths = (
            published.parent,
            published,
            published / "manifest.json",
            published / "snapshot.sqlite3",
        )
        for path in paths:
            assert path.stat().st_mode & 0o077 == 0


def test_insecure_snapshot_directory_is_refused(tmp_path, monkeypatch):
    database_path = tmp_path / "active.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE specimen (value TEXT)")
    root = tmp_path / "migration-snapshots"
    root.mkdir()
    monkeypatch.setattr(snapshots, "ensure_user_private_directory", lambda path: path)
    if os.name == "posix":
        root.chmod(0o755)
        with pytest.raises(SnapshotError, match="owner-only"):
            create_safety_snapshot(database_path, tmp_path)
        assert list(root.iterdir()) == []
        with pytest.raises(SnapshotError, match="owner-only"):
            select_recovery_snapshot(tmp_path)
