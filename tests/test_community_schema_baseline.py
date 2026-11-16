"""Baseline checks use disposable file-backed databases, never runtime data."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError

from scanner.db import Base
from tests.community_schema_fixture import create_unversioned_fixture
from tests.community_schema_support import baseline_specification, schema_specification

BASELINE_PATH = Path(__file__).parent / "fixtures/community/unversioned-v1.json"


@pytest.fixture
def specimen(tmp_path):
    engine = create_unversioned_fixture(tmp_path / "unversioned.db")
    try:
        yield engine
    finally:
        engine.dispose()


def test_snapshot_includes_constraints_indexes_defaults_and_foreign_keys(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'unversioned.db'}")
    try:
        Base.metadata.create_all(engine)
        tables = schema_specification(engine)["tables"]
        assert "ix_saved_reviews_target" in {
            index["name"] for index in tables["saved_reviews"]["indexes"]
        }
        assert any(
            constraint["name"] == "ck_saved_reviews_revision"
            for constraint in tables["saved_reviews"]["checks"]
        )
        assert (
            next(
                column
                for column in tables["saved_reviews"]["columns"]
                if column["name"] == "revision"
            )["default"]
            == "'0'"
        )
        assert any(
            foreign_key["referred_table"] == "saved_reviews"
            for foreign_key in tables["review_filters"]["foreign_keys"]
        )
    finally:
        engine.dispose()


def test_specimen_matches_pinned_schema_and_required_seeds(specimen, tmp_path):
    expected = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert baseline_specification(specimen) == expected
    with specimen.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.execute(text("SELECT id, scan_number, origin FROM scans")).all() == [
            ("legacy-scan-001", 7, "generated")
        ]
        assert connection.execute(
            text(
                "SELECT id, revision, edl_content_revision, mcf_content_revision FROM saved_reviews"
            )
        ).one() == ("review-legacy-001", 2, 1, 0)
        assert connection.execute(
            text(
                "SELECT id, source_scan_id, source_finding_id, selected, start_seconds "
                "FROM review_filters"
            )
        ).one() == ("review-filter-001", "legacy-scan-001", 101, 0, 15.0)
        assert connection.execute(
            text("SELECT start_seconds, end_seconds, end_mode FROM review_synchronizations")
        ).one() == (5.0, 120.0, "file_end")
        assert (
            connection.execute(text("SELECT username FROM admin_users")).scalar() == "fixture-admin"
        )
        assert connection.execute(text("SELECT COUNT(*) FROM filter_selection_rules")).scalar() == 1
    assert "scanner_version" not in {
        column["name"] for column in expected["tables"]["scans"]["columns"]
    }
    assert "alembic_version" not in expected["tables"]
    assert len(expected["required_seeds"]["mcf_severity_defaults"]) == 97
    assert all(row[3] == "medium" for row in expected["required_seeds"]["mcf_severity_defaults"])

    second = create_unversioned_fixture(tmp_path / "second.db")
    try:
        assert baseline_specification(second) == expected
    finally:
        second.dispose()


@pytest.mark.parametrize(
    "change", ["index", "constraint", "missing_column", "future_column", "table", "seed"]
)
def test_altered_schema_or_seeds_do_not_match_pinned_baseline(specimen, change):
    expected = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    with specimen.begin() as connection:
        if change == "index":
            connection.exec_driver_sql("DROP INDEX ix_saved_reviews_target")
        elif change == "constraint":
            connection.exec_driver_sql("DROP TABLE review_synchronizations")
            connection.exec_driver_sql(
                "CREATE TABLE review_synchronizations (review_id VARCHAR PRIMARY KEY, "
                "start_seconds FLOAT, end_seconds FLOAT, end_mode VARCHAR)"
            )
        elif change == "missing_column":
            connection.exec_driver_sql("ALTER TABLE scans DROP COLUMN media_provider_id")
        elif change == "future_column":
            connection.exec_driver_sql("ALTER TABLE scans ADD COLUMN future_version TEXT")
        elif change == "table":
            connection.exec_driver_sql("CREATE TABLE unknown_extension (id INTEGER PRIMARY KEY)")
        else:
            connection.exec_driver_sql("DELETE FROM mcf_severity_defaults WHERE position = 0")
    assert baseline_specification(specimen) != expected


def test_invalid_sqlite_file_is_not_accepted_as_a_schema(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a SQLite database")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with pytest.raises(DatabaseError):
            schema_specification(engine)
    finally:
        engine.dispose()


def test_fixture_generator_refuses_to_replace_existing_database(tmp_path):
    path = tmp_path / "already-here.db"
    path.write_bytes(b"untouched")
    with pytest.raises(FileExistsError):
        create_unversioned_fixture(path)
    assert path.read_bytes() == b"untouched"
