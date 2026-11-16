"""MU-02: admission requires 0010's never-reused user IDs, not only its reflected shape."""

import re
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine

from scanner.persistence.migrations import adoption, preflight, schema
from scanner.persistence.migrations.runner import migrate_fresh_database


def _rebuild_users(path, keyword):
    """Rebuild ``users`` with ``keyword`` in place of AUTOINCREMENT, keeping rows and indexes."""
    with closing(sqlite3.connect(path, isolation_level=None)) as database:
        (table,) = database.execute("SELECT sql FROM sqlite_master WHERE name = 'users'").fetchone()
        indexes = [
            sql
            for (sql,) in database.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND tbl_name = 'users' AND sql IS NOT NULL"
            )
        ]
        rebuilt = re.sub(r'^CREATE TABLE "?users"?', "CREATE TABLE users_rebuilt", table, count=1)
        assert rebuilt.count("AUTOINCREMENT") == 1
        database.execute("PRAGMA foreign_keys = OFF")
        database.execute("BEGIN")
        database.execute(rebuilt.replace("AUTOINCREMENT", keyword))
        database.execute("INSERT INTO users_rebuilt SELECT * FROM users")
        database.execute("DROP TABLE users")
        database.execute("ALTER TABLE users_rebuilt RENAME TO users")
        for sql in indexes:
            database.execute(sql)
        database.execute("COMMIT")


def _specification(path):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            return schema.schema_specification(connection, versioned=True)
    finally:
        engine.dispose()


def _without_autoincrement(specification):
    for table in specification["tables"].values():
        table.pop("autoincrement", None)
    return specification


@pytest.fixture
def head(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    migrate_fresh_database(data / "scanner.db", data)
    return data / "scanner.db", data


@pytest.mark.parametrize("keyword", ["", "/* AUTOINCREMENT */", "-- AUTOINCREMENT\n"])
def test_head_without_autoincrement_is_refused_without_snapshot_or_journal(head, keyword):
    path, data = head
    expected = _specification(path)
    _rebuild_users(path, keyword)
    assert _without_autoincrement(_specification(path)) == _without_autoincrement(expected)
    before = path.read_bytes()

    for admit in (
        lambda: adoption._inspect_database(path),
        lambda: preflight.inspect_schema(path, data),
        lambda: adoption.upgrade_existing_database(path, data),
    ):
        with pytest.raises(ValueError, match="Unsupported versioned database schema"):
            admit()
    assert path.read_bytes() == before
    assert not (data / adoption.JOURNAL_NAME).exists()
    assert not list((data / "migration-snapshots").glob("snapshot-*"))


def test_head_rebuilt_with_autoincrement_is_still_admitted(head):
    path, data = head
    _rebuild_users(path, "AUTOINCREMENT")
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert adoption.upgrade_existing_database(path, data) is None


def test_only_users_and_audit_events_record_autoincrement():
    for revision in (
        adoption.PREVIOUS_REVISION,
        adoption.COMMUNITY_REVISION,
        adoption.MEDIA_IDENTITY_REVISION,
        adoption.AUDIO_TRACK_REVISION,
        adoption.COMMUNITY_EVIDENCE_REVISION,
        adoption.PUBLICATIONS_REVISION,
        adoption.API_KEY_REVISION,
        adoption.READABLE_API_KEY_REVISION,
    ):
        tables = schema._versioned_specification(revision)["tables"]
        assert not any("autoincrement" in table for table in tables.values()), revision
    head = schema._versioned_specification(adoption.HEAD_REVISION)["tables"]
    assert [name for name, table in head.items() if "autoincrement" in table] == [
        "audit_events",
        "users",
    ]
    assert head["users"]["autoincrement"] is True
    assert head["audit_events"]["autoincrement"] is True
