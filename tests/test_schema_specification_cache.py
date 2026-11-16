"""Cached schema reflection must never hide a schema or seed change."""

import pytest
from sqlalchemy import create_engine, text

from scanner.persistence.migrations import schema
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.persistence.migrations.schema import schema_specification, verify_current_schema


@pytest.fixture
def connection(tmp_path):
    migrate_fresh_database(tmp_path / "scanner.db", tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'scanner.db'}")
    with engine.connect() as connection:
        yield connection
    engine.dispose()


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE users ADD COLUMN extra TEXT",
        "CREATE INDEX extra_index ON users (id)",
        "DROP INDEX uq_users_single_owner",
        "CREATE TABLE extra (id INTEGER PRIMARY KEY)",
    ],
)
def test_schema_change_after_a_cached_verification_is_refused(connection, change):
    verify_current_schema(connection)
    before = schema_specification(connection, versioned=True)

    connection.execute(text(change))

    assert schema_specification(connection, versioned=True) != before
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        verify_current_schema(connection)


def test_seed_rows_are_read_live_on_every_call(connection):
    before = schema_specification(connection)

    connection.execute(text("UPDATE filter_settings SET revision = revision + 1"))

    after = schema_specification(connection)
    assert after["tables"] == before["tables"]
    assert after["required_seeds"] != before["required_seeds"]


def test_mutating_a_returned_specification_does_not_change_the_cache(connection):
    first = schema_specification(connection, versioned=True)
    first["tables"].clear()

    assert schema_specification(connection, versioned=True)["tables"]


def test_versioned_and_unversioned_shapes_are_cached_separately(connection):
    assert "alembic_version" in schema_specification(connection)["tables"]
    assert "alembic_version" not in schema_specification(connection, versioned=True)["tables"]
    assert "alembic_version" in schema_specification(connection)["tables"]


def test_the_cache_stays_bounded(connection, monkeypatch):
    monkeypatch.setattr(schema, "_table_cache", {})
    for index in range(schema._TABLE_CACHE_LIMIT + 5):
        connection.execute(text(f"CREATE TABLE extra_{index} (id INTEGER PRIMARY KEY)"))
        schema_specification(connection)

    assert len(schema._table_cache) == schema._TABLE_CACHE_LIMIT
