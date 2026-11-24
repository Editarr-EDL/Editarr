"""Runtime FK enforcement must not change coordinated migration and restore connections."""

import hashlib
import sqlite3

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

import scanner.db as runtime_db
from scanner.backup.archive import export_backup
from scanner.backup.snapshots import SnapshotError, create_safety_snapshot
from scanner.persistence.migrations import preflight
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.settings.config import Settings
from tests.test_scan_access_http import app as app


@pytest.mark.real_migrations
def test_migration_connections_stay_fk_off_after_runtime_engine_exists(monkeypatch, tmp_path):
    configuration = Settings(data_dir=tmp_path)
    migrate_fresh_database(tmp_path / "scanner.db", tmp_path)
    monkeypatch.setattr(runtime_db, "settings", configuration)
    engine = runtime_db.get_engine()
    observed = []

    def observe(connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith(("CREATE TABLE", "ALTER TABLE", "DROP TABLE")):
            observed.append(
                connection.connection.driver_connection.execute("PRAGMA foreign_keys").fetchone()[0]
            )

    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        event.listen(Engine, "before_cursor_execute", observe)
        try:
            migrate_fresh_database(tmp_path / "fresh-upgrade.db", tmp_path)
        finally:
            event.remove(Engine, "before_cursor_execute", observe)
        assert observed and set(observed) == {0}
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        engine.dispose()


def test_existing_orphans_are_refused_without_automatic_repair(monkeypatch, tmp_path):
    configuration = Settings(data_dir=tmp_path)
    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT INTO community_operations "
            "(operation_id, service_url, kind, state, scan_id, updated_at) "
            "VALUES ('orphan', 'https://community.example', 'submission', 'pending', "
            "'missing-scan', '2026-10-24T00:00:00Z')"
        )
    before = hashlib.sha256(path.read_bytes()).digest()
    monkeypatch.setattr(preflight, "settings", configuration)

    for admission in (preflight.prepare_database, preflight.require_supported_schema):
        with pytest.raises(ValueError, match="foreign key check failed"):
            admission()
    with pytest.raises(SnapshotError, match="foreign key validation failed"):
        create_safety_snapshot(path, tmp_path)
    destination = tmp_path / "backups" / "invalid.backup"
    with pytest.raises(SnapshotError, match="foreign key validation failed"):
        export_backup(path, tmp_path, destination)

    assert hashlib.sha256(path.read_bytes()).digest() == before
    assert not destination.exists()
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT scan_id FROM community_operations").fetchall() == [
            ("missing-scan",)
        ]
