"""Editable filter values survive admission, upgrades, and settings restore."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import db, worker
from scanner.backup.activation import activate_restore
from scanner.backup.archive import export_backup
from scanner.backup.restore import preview_restore
from scanner.models import FilterDefaultsUpdate, FilterSelectionRule
from scanner.persistence.migrations import adoption, preflight, schema
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.persistence.migrations.schema import verify_current_schema
from scanner.persistence.repository.filter_preferences import set_default_mode, set_mode
from scanner.persistence.repository.repository_settings import (
    get_structured_filter_defaults_settings,
    update_filter_defaults_settings,
)
from scanner.settings.config import Settings
from scanner.settings.filter_policy.updates import FilterModeUpdate
from tests.community_schema_fixture import create_unversioned_fixture
from tests.explicit_filter_modes_support import legacy_instance_severities
from tests.multi_user_access_support import add_linked_owner


@pytest.fixture
def edited(tmp_path, monkeypatch):
    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as session:
            add_linked_owner(session, "scratch-owner")
            before = get_structured_filter_defaults_settings(session)
            values = before.model_dump()
            values["selection_rules"] = [
                FilterSelectionRule(
                    id="a" * 32, disposition="deny", evidence_source="audio"
                ).model_dump()
            ]
            values["severity_defaults"][0]["severity"] = "high"
            saved = update_filter_defaults_settings(session, FilterDefaultsUpdate(**values))
            assert saved.revision == before.revision + 1
            assert saved.severity_defaults[0].severity == "high"
        configuration = Settings(data_dir=tmp_path)
        monkeypatch.setattr(preflight, "settings", configuration)
        monkeypatch.setattr(worker, "settings", configuration)
        yield path, engine, saved
    finally:
        engine.dispose()


def _assert_values(engine, expected):
    with Session(engine) as session:
        assert get_structured_filter_defaults_settings(session) == expected


def test_edited_defaults_pass_versioned_schema_admission(edited):
    path, engine, saved = edited
    with engine.connect() as connection:
        verify_current_schema(connection)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    _assert_values(engine, saved)


@pytest.mark.parametrize(
    "revision",
    [
        None,
        adoption.BASELINE_REVISION,
        adoption.PREVIOUS_REVISION,
        adoption.COMMUNITY_REVISION,
        adoption.MEDIA_IDENTITY_REVISION,
        adoption.AUDIO_TRACK_REVISION,
        adoption.COMMUNITY_EVIDENCE_REVISION,
        adoption.PUBLICATIONS_REVISION,
        adoption.API_KEY_REVISION,
        adoption.READABLE_API_KEY_REVISION,
        adoption.USER_ACCOUNTS_REVISION,
        adoption.ACTIVITY_REVISION,
        adoption.PERSONAL_FILTER_REVISION,
        adoption.COMMUNITY_IDENTITY_REVISION,
        adoption.MEDIA_ACCESS_REVISION,
        adoption.SEALED_API_KEY_REVISION,
        adoption.PERSONAL_FILTER_RULES_REVISION,
        adoption.USER_PROFILES_REVISION,
        adoption.HEAD_REVISION,
    ],
)
@pytest.mark.parametrize(
    "corruption",
    [
        "package-revision",
        "package-severity",
        "package-fingerprint",
        "negative-revision",
        "fractional-revision",
        "text-revision",
        "invalid-severity",
    ],
)
def test_every_admission_path_checks_package_and_mutable_domains(
    tmp_path, monkeypatch, revision, corruption
):
    path = tmp_path / "scanner.db"
    engine = (
        create_unversioned_fixture(path) if revision is None else create_engine(f"sqlite:///{path}")
    )
    try:
        with engine.begin() as connection:
            if revision is not None:
                configuration = Config()
                configuration.set_main_option(
                    "script_location", str(Path(adoption.__file__).parent)
                )
                configuration.attributes["connection"] = connection
                command.upgrade(configuration, revision)
    finally:
        engine.dispose()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE filter_settings SET revision=7")
        connection.execute("UPDATE mcf_severity_defaults SET severity='high'")
        connection.commit()
    assert adoption._inspect_database(path) == revision
    if corruption.startswith("package-"):
        baseline = schema.frozen_baseline()
        if corruption == "package-revision":
            baseline["required_seeds"]["filter_settings"][0][1] = 8080
        elif corruption == "package-severity":
            baseline["required_seeds"]["mcf_severity_defaults"][0][3] = "high"
        else:
            baseline["schema_fingerprint"] = "0" * 64
        original = Path.read_text
        baseline_path = Path(schema.__file__).parent / "versions/baseline-v1.json"

        def read_text(resource, *args, **kwargs):
            return (
                json.dumps(baseline)
                if resource == baseline_path
                else original(resource, *args, **kwargs)
            )

        monkeypatch.setattr(Path, "read_text", read_text)
    else:
        statement = {
            "negative-revision": "UPDATE filter_settings SET revision=-1",
            "fractional-revision": "UPDATE filter_settings SET revision=1.5",
            "text-revision": "UPDATE filter_settings SET revision='invalid'",
            "invalid-severity": "UPDATE mcf_severity_defaults SET severity='invalid'",
        }[corruption]
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(statement)
            connection.commit()
    before = path.read_bytes(), path.stat().st_mtime_ns
    with pytest.raises(ValueError):
        adoption._inspect_database(path)
    with pytest.raises(ValueError):
        adoption.upgrade_existing_database(path, tmp_path)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not (tmp_path / adoption.JOURNAL_NAME).exists()
    assert not (tmp_path / "migration-snapshots").exists()


def test_edited_defaults_survive_prepare_and_init_db(edited):
    _, engine, saved = edited
    assert preflight.prepare_database() == adoption.HEAD_REVISION
    db.init_db()
    _assert_values(engine, saved)


def test_worker_admits_edited_defaults_before_dispatch(edited, monkeypatch):
    _, engine, saved = edited
    assert preflight.require_supported_schema() == adoption.HEAD_REVISION
    monkeypatch.setattr(worker, "_run_scan_worker", lambda *_args: 37)
    assert worker.run_scan_worker("synthetic-scan", "synthetic-token") == 37
    _assert_values(engine, saved)


def test_edited_defaults_export_preview_and_activation(edited, tmp_path):
    path, _, saved = edited
    target = tmp_path / "target"
    target.mkdir()
    target_db = target / "scanner.db"
    migrate_fresh_database(target_db, target)
    archive = tmp_path / "full.backup"
    export_backup(path, tmp_path, archive)
    with preview_restore(archive, target) as preview:
        assert preview.plan["kind"] == "full"
        activate_restore(preview, target_db, target, confirmed=True)
    assert adoption._inspect_database(target_db) == adoption.HEAD_REVISION
    engine = create_engine(f"sqlite:///{target_db}")
    try:
        _assert_values(engine, saved)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM mcf_severity_defaults WHERE position=0",
        "INSERT INTO mcf_severity_defaults (settings_id, subcategory, position, severity) "
        "VALUES (1, 'unknown', 999, 'medium')",
        "UPDATE mcf_severity_defaults SET position=999 WHERE position=0",
        "UPDATE mcf_severity_defaults SET settings_id=2 WHERE position=0",
        "UPDATE mcf_severity_defaults SET subcategory='unknown' WHERE position=0",
        "DELETE FROM filter_settings",
        "INSERT INTO filter_settings (id, revision, updated_at, default_mode) "
        "VALUES (2, 0, CURRENT_TIMESTAMP, 'live')",
        "ALTER TABLE filter_settings ADD COLUMN unexpected TEXT",
    ],
)
def test_versioned_seed_inventory_and_schema_still_fail_closed(tmp_path, statement):
    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    with sqlite3.connect(path) as connection:
        try:
            connection.execute(statement)
        except sqlite3.IntegrityError:
            return
    before = path.read_bytes()
    with pytest.raises(ValueError):
        adoption.upgrade_existing_database(path, tmp_path)
    assert path.read_bytes() == before
    assert not (tmp_path / adoption.JOURNAL_NAME).exists()
    assert not (tmp_path / "migration-snapshots").exists()


@pytest.mark.parametrize("column", ["revision", "severity"])
def test_unversioned_baseline_adopts_edited_defaults_without_losing_values(tmp_path, column):
    path = tmp_path / "baseline.db"
    engine = create_unversioned_fixture(path)
    try:
        with engine.begin() as connection:
            statement = (
                "UPDATE filter_settings SET revision=1"
                if column == "revision"
                else "UPDATE mcf_severity_defaults SET severity='high' WHERE position=0"
            )
            connection.exec_driver_sql(statement)
            before = {
                table: connection.exec_driver_sql(f"SELECT * FROM {table}").fetchall()
                for table in ("filter_settings", "mcf_severity_defaults")
            }
    finally:
        engine.dispose()
    generation = adoption.upgrade_existing_database(path, tmp_path)
    adoption.validate_safety_snapshot(generation)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert adoption._inspect_database(generation / "snapshot.sqlite3") is None
    for database in (path, generation / "snapshot.sqlite3"):
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT id, revision FROM filter_settings").fetchall() == [
                tuple(row[:2]) for row in before["filter_settings"]
            ]
            rows = connection.execute("SELECT * FROM mcf_severity_defaults").fetchall()
            if database == path:
                rows = legacy_instance_severities(rows)
            assert rows == [tuple(row) for row in before["mcf_severity_defaults"]]


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE filter_settings ADD COLUMN unexpected TEXT",
        "CREATE INDEX unexpected ON filter_settings (revision)",
        "CREATE TABLE unexpected (id INTEGER)",
        "DELETE FROM mcf_severity_defaults WHERE position=0",
        "INSERT INTO mcf_severity_defaults VALUES (1, 'unknown', 97, 'medium')",
        "UPDATE mcf_severity_defaults SET position=999 WHERE position=0",
        "UPDATE mcf_severity_defaults SET settings_id=2 WHERE position=0",
        "UPDATE mcf_severity_defaults SET subcategory='unknown' WHERE position=0",
        "DELETE FROM filter_settings",
    ],
)
def test_unversioned_shape_and_seed_identity_changes_never_stamp(tmp_path, statement):
    path = tmp_path / "scanner.db"
    create_unversioned_fixture(path).dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE filter_settings SET revision=7")
        connection.execute("UPDATE mcf_severity_defaults SET severity='high'")
        connection.execute(statement)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        adoption.upgrade_existing_database(path, tmp_path)
    assert path.read_bytes() == before
    assert not (tmp_path / adoption.JOURNAL_NAME).exists()
    assert not (tmp_path / "migration-snapshots").exists()
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='alembic_version'"
            ).fetchone()
            is None
        )


@pytest.mark.parametrize(
    "revision",
    [
        "unversioned-v1",
        "0001_current_baseline",
        "0002_scan_provenance",
        "0003_local_community",
        "0004_media_identity",
        "0005_audio_tracks",
        "0006_community_evidence",
        "0007_community_publications",
        "0008_api_key",
        "0009_readable_api_key",
        "0010_user_accounts",
        "0011_activity_ownership",
        "0012_personal_filter_values",
        "0013_community_identities",
        "0014_media_access_exceptions",
        "0015_sealed_api_keys",
        "0016_personal_filter_rules",
        "0017_user_profiles",
    ],
)
def test_each_older_version_upgrades_edited_seeds_with_recoverable_snapshot(tmp_path, revision):
    path = tmp_path / "scanner.db"
    engine = (
        create_unversioned_fixture(path)
        if revision == "unversioned-v1"
        else create_engine(f"sqlite:///{path}")
    )
    try:
        with engine.begin() as connection:
            if revision != "unversioned-v1":
                configuration = Config()
                configuration.set_main_option(
                    "script_location", str(Path(adoption.__file__).parent)
                )
                configuration.attributes["connection"] = connection
                command.upgrade(configuration, revision)
            connection.exec_driver_sql("UPDATE filter_settings SET revision=7")
            connection.exec_driver_sql(
                "UPDATE mcf_severity_defaults SET severity='high' WHERE position=0"
            )
    finally:
        engine.dispose()
    expected_revision = None if revision == "unversioned-v1" else revision
    assert adoption._inspect_database(path) == expected_revision
    generation = adoption.upgrade_existing_database(path, tmp_path)
    assert generation.is_dir()
    adoption.validate_safety_snapshot(generation)
    journal = json.loads((tmp_path / adoption.JOURNAL_NAME).read_text())
    assert journal["status"] == "complete"
    assert journal["from_revision"] == revision
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert adoption._inspect_database(generation / "snapshot.sqlite3") == expected_revision
    for database in (path, generation / "snapshot.sqlite3"):
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT revision FROM filter_settings").fetchall() == [(7,)]
            assert connection.execute(
                "SELECT severity FROM mcf_severity_defaults WHERE position=0"
            ).fetchall() == [("high",)]


def test_other_mutable_filter_tables_and_default_mode_are_data(edited):
    path, engine, saved = edited
    with Session(engine) as session:
        assert set_default_mode(session, "copy") == "copy"
        update = FilterModeUpdate(revision=0, mode="copy")
        set_mode(session, 1, update, self_service=False, now=datetime.now(timezone.utc))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE user_filter_preferences SET revision=3, policy_revision=2")
        connection.execute(
            "INSERT INTO user_filter_severity_overrides (user_id, subcategory, severity) "
            "VALUES (1, 'swearing', 'low')"
        )
        connection.execute(
            "INSERT INTO user_filter_personal_rules (user_id, id, position, disposition, category) "
            "VALUES (1, 'personal-rule', 0, 'deny', 'language')"
        )
    with sqlite3.connect(path) as connection:
        personal = {
            table: connection.execute(f'SELECT * FROM "{table}"').fetchall()
            for table in (
                "user_filter_preferences",
                "user_filter_copy_rules",
                "user_filter_copy_severities",
                "user_filter_severity_overrides",
                "user_filter_personal_rules",
            )
        }
    assert all(personal.values())
    assert preflight.prepare_database() == adoption.HEAD_REVISION
    assert preflight.require_supported_schema() == adoption.HEAD_REVISION
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT default_mode FROM filter_settings").fetchone() == (
            "copy",
        )
        for table, rows in personal.items():
            assert connection.execute(f'SELECT * FROM "{table}"').fetchall() == rows
    _assert_values(engine, saved)
