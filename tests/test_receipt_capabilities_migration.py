"""Upgrade to 0038 removes only the retired import-receipt capability grants."""

import sqlite3

import pytest
from sqlalchemy import create_engine

from scanner.backup.snapshots import validate_safety_snapshot
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import upgrade_connection
from tests.community_identity_migration_support import no_network as no_network

pytestmark = pytest.mark.real_migrations

SOURCE = "0037_community_master_sets"
_TIME = "2026-01-01"
_RETIRED = ("community.receipts.recover.own", "community.receipts.recover.other")
_KEPT = "community.download"
_DEFAULTS = "SELECT role, capability, granted FROM role_capability_defaults ORDER BY 1, 2"
_OVERRIDES = "SELECT user_id, capability, effect FROM user_capability_overrides ORDER BY 1, 2"


def _seed_0037(path):
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
                "INSERT INTO role_capability_defaults (role, capability, granted, updated_at) "
                "VALUES (?, ?, ?, ?)",
                [
                    ("user", _RETIRED[0], 0, _TIME),
                    ("admin", _RETIRED[1], 0, _TIME),
                    ("user", _KEPT, 0, _TIME),
                ],
            )
            db.exec_driver_sql(
                "INSERT INTO user_capability_overrides (user_id, capability, effect, updated_at) "
                "VALUES (2, ?, ?, ?)",
                [
                    (_RETIRED[0], "deny", _TIME),
                    (_RETIRED[1], "allow", _TIME),
                    (_KEPT, "allow", _TIME),
                ],
            )
    finally:
        engine.dispose()


def _read(path, query):
    with sqlite3.connect(path) as database:
        return database.execute(query).fetchall()


def test_upgrade_removes_retired_grants_and_keeps_the_rest_recoverable(tmp_path):
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700)
    _seed_0037(path)
    defaults, overrides = _read(path, _DEFAULTS), _read(path, _OVERRIDES)
    assert adoption._inspect_database(path) == SOURCE

    generation = adoption.upgrade_existing_database(path, path.parent)

    validate_safety_snapshot(generation)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert _read(path, _DEFAULTS) == [("user", _KEPT, 0)]
    assert _read(path, _OVERRIDES) == [(2, _KEPT, "allow")]
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert adoption._inspect_database(recovered) == SOURCE
    assert (_read(recovered, _DEFAULTS), _read(recovered, _OVERRIDES)) == (defaults, overrides)
