"""Upgrade to 0039 adds review community snapshots and the hide preference, losing nothing."""

import sqlite3

import pytest
from sqlalchemy import create_engine

from scanner.backup.snapshots import validate_safety_snapshot
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import upgrade_connection
from tests.community_identity_migration_support import no_network as no_network

pytestmark = pytest.mark.real_migrations

SOURCE = "0038_retire_receipt_capabilities"
_TIME = "2026-01-01"
_PREFERENCES = "SELECT * FROM community_master_preferences ORDER BY owner_id"


def _seed(path):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as db:
            upgrade_connection(db, SOURCE)
            db.exec_driver_sql(
                "INSERT INTO users (id, role, status, local_export_id, permission_revision, "
                "created_at, updated_at) VALUES (?, ?, 'active', ?, 1, ?, ?)",
                [
                    (1, "owner", "local-" + "e" * 32, _TIME, _TIME),
                    (2, "user", "local-" + "f" * 32, _TIME, _TIME),
                ],
            )
            db.exec_driver_sql(
                "INSERT INTO community_master_preferences "
                "(owner_id, confidence_threshold, show_low_confidence) VALUES (?, ?, ?)",
                [(1, 0.25, 1), (2, 0.0, 0)],
            )
    finally:
        engine.dispose()


def _read(path, query):
    with sqlite3.connect(path) as database:
        return database.execute(query).fetchall()


def test_upgrade_keeps_preferences_and_adds_empty_snapshots_recoverably(tmp_path):
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700)
    _seed(path)
    before = _read(path, _PREFERENCES)
    assert adoption._inspect_database(path) == SOURCE

    generation = adoption.upgrade_existing_database(path, path.parent)

    validate_safety_snapshot(generation)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert _read(path, _PREFERENCES) == [(*row, 0) for row in before]
    assert _read(path, "SELECT * FROM review_community_sources") == []
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert adoption._inspect_database(recovered) == SOURCE
    assert _read(recovered, _PREFERENCES) == before


def test_downgrade_refuses_to_discard_a_hide_preference(tmp_path):
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = tmp_path / "scanner.db"
    _seed(path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as db:
            upgrade_connection(db, adoption.HEAD_REVISION)
            db.exec_driver_sql(
                "UPDATE community_master_preferences SET hide_downvoted = 1 WHERE owner_id = 2"
            )
        migration = import_module(
            "scanner.persistence.migrations.versions.0039_review_community_sources"
        )
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            with pytest.raises(RuntimeError, match="discarding preferences"):
                migration.downgrade()
    finally:
        engine.dispose()
    assert _read(path, "SELECT hide_downvoted FROM community_master_preferences") == [
        (0,),
        (1,),
    ]


def test_a_database_from_the_earlier_published_0039_upgrades_keeping_its_hide_values(tmp_path):
    """The first published 0039 named the preference `hide_rejected_downvoted`."""
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700)
    _seed(path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as db:
            upgrade_connection(db, "0039_review_community_sources")
            db.exec_driver_sql(
                "ALTER TABLE community_master_preferences "
                "RENAME COLUMN hide_downvoted TO hide_rejected_downvoted"
            )
            db.exec_driver_sql(
                "UPDATE community_master_preferences SET hide_rejected_downvoted = 1 "
                "WHERE owner_id = 2"
            )
    finally:
        engine.dispose()
    before = _read(path, _PREFERENCES)
    assert adoption._inspect_database(path) == "0039_review_community_sources"

    generation = adoption.upgrade_existing_database(path, path.parent)

    validate_safety_snapshot(generation)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert _read(path, _PREFERENCES) == before
    assert _read(
        path, "SELECT owner_id, hide_downvoted FROM community_master_preferences ORDER BY owner_id"
    ) == [(1, 0), (2, 1)]
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert adoption._inspect_database(recovered) == "0039_review_community_sources"
    assert _read(recovered, _PREFERENCES) == before


def test_a_mismatched_hide_column_is_still_refused(tmp_path):
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700)
    _seed(path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as db:
            upgrade_connection(db, "0039_review_community_sources")
            db.exec_driver_sql(
                "ALTER TABLE community_master_preferences "
                "RENAME COLUMN hide_downvoted TO hide_something_else"
            )
    finally:
        engine.dispose()
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption._inspect_database(path)
