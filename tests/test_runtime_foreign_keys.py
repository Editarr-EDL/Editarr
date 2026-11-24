"""Runtime deletion must preserve durable community evidence and FK integrity."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

import scanner.db as runtime_db
from scanner.db import CommunityOperationRow, ScanRow
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.repository import ScanReferencedError, delete_scan, get_scan_references
from scanner.settings.config import Settings
from tests.multi_user_access_support import OWNER
from tests.runtime_fk_inventory import EXPECTED_FOREIGN_KEYS
from tests.test_scan_access_http import app as app

SERVICE = "https://community.test"


@pytest.mark.parametrize("kind", ["publish", "submission", "filter_vote"])
@pytest.mark.parametrize("state", ["pending", "submitted", "acknowledged", "failed"])
def test_only_in_progress_operations_block_scan_deletion(app, kind, state):
    scan_id, _ = app.seed_scan(OWNER, "operation-target", status="complete")
    with app.accounts.factory() as database:
        database.add(
            CommunityOperationRow(
                operation_id="retained-operation",
                service_url=SERVICE,
                kind=kind,
                state=state,
                scan_id=scan_id,
                updated_at="2026-10-24T00:00:00Z",
            )
        )
        database.commit()
        blocked = state in {"pending", "submitted"}
        assert get_scan_references(database, scan_id).community_in_progress == blocked
        if blocked:
            with pytest.raises(ScanReferencedError, match="still in progress"):
                delete_scan(database, scan_id)
        else:
            assert delete_scan(database, scan_id)
        assert (database.get(ScanRow, scan_id) is not None) == blocked
        assert database.get(CommunityOperationRow, "retained-operation").state == state
        assert database.execute(text("PRAGMA foreign_key_check")).all() == []


def test_runtime_connections_enforce_foreign_keys_including_after_reconnect(monkeypatch, tmp_path):
    configuration = Settings(data_dir=tmp_path)
    migrate_fresh_database(tmp_path / "scanner.db", tmp_path)
    monkeypatch.setattr(runtime_db, "settings", configuration)
    engine = runtime_db.get_engine()
    try:
        for _attempt in range(2):
            with engine.connect() as first, engine.connect() as second:
                for connection in (first, second):
                    assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            engine.dispose()
    finally:
        engine.dispose()


def test_every_head_foreign_key_has_a_reviewed_deletion_policy(tmp_path):
    from sqlalchemy import create_engine

    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        inspector = inspect(engine)
        actual = {
            "|".join(
                (
                    table,
                    ",".join(foreign_key["constrained_columns"]),
                    foreign_key["referred_table"],
                    ",".join(foreign_key["referred_columns"]),
                    foreign_key["options"].get("ondelete", "NO ACTION"),
                )
            )
            for table in inspector.get_table_names()
            for foreign_key in inspector.get_foreign_keys(table)
        }
        assert actual == EXPECTED_FOREIGN_KEYS
        assert {
            "|".join(
                (
                    table.name,
                    ",".join(element.parent.name for element in constraint.elements),
                    constraint.referred_table.name,
                    ",".join(element.column.name for element in constraint.elements),
                    constraint.ondelete or "NO ACTION",
                )
            )
            for table in runtime_db.Base.metadata.tables.values()
            for constraint in table.foreign_key_constraints
        } == EXPECTED_FOREIGN_KEYS
    finally:
        engine.dispose()


@pytest.mark.parametrize("action", ["CASCADE", "RESTRICT", "NO ACTION", "SET NULL"])
def test_runtime_enforces_declared_delete_actions(monkeypatch, tmp_path, action):
    configuration = Settings(data_dir=tmp_path)
    migrate_fresh_database(tmp_path / "scanner.db", tmp_path)
    monkeypatch.setattr(runtime_db, "settings", configuration)
    engine = runtime_db.get_engine()
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE specimen_parent (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE specimen_child (id INTEGER PRIMARY KEY, parent_id INTEGER "
                f"REFERENCES specimen_parent(id) ON DELETE {action})"
            )
            connection.exec_driver_sql("INSERT INTO specimen_parent VALUES (1)")
            connection.exec_driver_sql("INSERT INTO specimen_child VALUES (1, 1)")
        if action in ("RESTRICT", "NO ACTION"):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.exec_driver_sql("DELETE FROM specimen_parent")
        else:
            with engine.begin() as connection:
                connection.exec_driver_sql("DELETE FROM specimen_parent")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert connection.exec_driver_sql("SELECT parent_id FROM specimen_child").all() == (
                [] if action == "CASCADE" else [(None,)] if action == "SET NULL" else [(1,)]
            )
    finally:
        engine.dispose()
