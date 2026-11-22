"""Compare a fresh packaged migration with the frozen unversioned schema."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from scanner.db import Base
from scanner.persistence.migrations.runner import migrate_fresh_database, upgrade_connection
from scanner.persistence.seeds import seed_filter_defaults
from tests.community_migration_support import frozen_baseline
from tests.community_schema_support import baseline_specification

pytestmark = pytest.mark.real_migrations


def test_fresh_migration_matches_supported_schema(tmp_path):
    database_path = tmp_path / "fresh.db"
    migrate_fresh_database(database_path, tmp_path)
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        migrated = baseline_specification(engine)
        assert migrated["tables"].pop("alembic_version")
        assert "scan_stage_coverage" in migrated["tables"]
        finding_columns = migrated["tables"]["scan_findings"]["columns"]
        assert {column["name"]: column["nullable"] for column in finding_columns}["confidence"]
        from scanner.persistence.migrations.schema import verify_current_schema

        with engine.connect() as connection:
            verify_current_schema(connection)
        assert inspect(engine).get_table_names().count("alembic_version") == 1
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                "0035_stable_media_identity"
            )
    finally:
        engine.dispose()


def test_fresh_migration_matches_current_orm_shape(tmp_path):
    migrated_path = tmp_path / "migrated.db"
    migrate_fresh_database(migrated_path, tmp_path)
    migrated = create_engine(f"sqlite:///{migrated_path}")
    orm = create_engine(f"sqlite:///{tmp_path / 'orm.db'}")
    try:
        Base.metadata.create_all(orm)
        actual = baseline_specification(migrated)
        actual["tables"].pop("alembic_version")
        assert actual == baseline_specification(orm)
    finally:
        migrated.dispose()
        orm.dispose()


def test_baseline_resource_is_frozen_from_css01_fixture():
    resource = Path(__file__).parents[1] / (
        "scanner/persistence/migrations/versions/baseline-v1.json"
    )
    baseline = json.loads(resource.read_text(encoding="utf-8"))
    assert baseline == frozen_baseline()
    assert baseline["schema_fingerprint"] == (
        "b8c923bc2fcf94e19e484581e67b10aa24108823c0736268e81535bf1bc00e3d"
    )
    assert len(baseline["statements"]) == 27


def test_repeat_upgrade_and_explicit_seeding_are_idempotent(tmp_path):
    path = tmp_path / "fresh.db"
    migrate_fresh_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            upgrade_connection(connection)
            seed_filter_defaults(connection)
            seed_filter_defaults(connection)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM filter_settings")).scalar() == 1
            assert (
                connection.execute(text("SELECT count(*) FROM mcf_severity_defaults")).scalar()
                == 97
            )
            assert connection.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("contents", [b"", b"not a SQLite database"])
def test_fresh_migration_never_opens_existing_database(tmp_path, contents):
    path = tmp_path / "existing.db"
    path.write_bytes(contents)
    with pytest.raises(FileExistsError):
        migrate_fresh_database(path, tmp_path)
    assert path.read_bytes() == contents


def test_migration_does_not_call_current_model_creation(tmp_path, monkeypatch):
    def refuse_model_creation(*_args, **_kwargs):
        raise AssertionError("Historical migration must not use current ORM metadata")

    monkeypatch.setattr(Base.metadata, "create_all", refuse_model_creation)
    path = tmp_path / "fresh.db"
    migrate_fresh_database(path, tmp_path)
    assert path.exists()


@pytest.mark.parametrize("table_sql", ["CREATE TABLE unknown_shape (id INTEGER)", None])
def test_upgrade_connection_refuses_existing_unversioned_shape(tmp_path, table_sql):
    path = tmp_path / "unversioned.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        if table_sql is None:
            Base.metadata.create_all(engine)
        else:
            with engine.begin() as connection:
                connection.exec_driver_sql(table_sql)
        with engine.begin() as connection:
            with pytest.raises(RuntimeError, match="verified adoption"):
                upgrade_connection(connection)
        assert "alembic_version" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
