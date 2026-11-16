"""What counts as an empty restore target, and the binding an empty installation keeps."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from scanner.backup import restore_target
from scanner.backup.activation import REPORT, activate_restore, recover_restore
from scanner.backup.archive import BackupError, export_backup
from scanner.backup.restore import backup_deployment, preview_restore
from scanner.backup.restore_target import (
    HAS_WORK,
    UNSUPPORTED_SCHEMA,
    RestoreRefused,
    require_empty_installation,
)
from scanner.persistence.migrations import adoption, schema
from scanner.persistence.migrations.runner import migrate_fresh_database
from tests.test_backup_activation import _ACTIVATE, _fixture, _restore


def _migrated(path, revision):
    """A freshly migrated installation at a packaged `revision`, as that release made it."""
    path.parent.mkdir(mode=0o700, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            configuration = Config()
            configuration.set_main_option("script_location", str(Path(adoption.__file__).parent))
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, revision)
    finally:
        engine.dispose()


def _files(directory):
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "revision",
    [
        adoption.BASELINE_REVISION,
        adoption.ACTIVITY_REVISION,
        adoption.COMMUNITY_IDENTITY_REVISION,
        adoption.HEAD_REVISION,
    ],
)
def test_a_freshly_migrated_installation_is_empty(tmp_path, revision):
    _migrated(tmp_path / "scanner.db", revision)
    require_empty_installation(tmp_path, tmp_path / "scanner.db")


def test_concurrent_checks_build_their_references_one_at_a_time(tmp_path):
    """Onboarding may start two flows at once; Alembic's context is process-global."""
    _migrated(tmp_path / "scanner.db", adoption.COMMUNITY_IDENTITY_REVISION)
    restore_target._fresh_seeds.cache_clear()
    schema._versioned_specification.cache_clear()
    with ThreadPoolExecutor(4) as pool:
        checks = [
            pool.submit(require_empty_installation, tmp_path, tmp_path / "scanner.db")
            for _ in range(4)
        ]
        assert [check.result() for check in checks] == [None] * 4


@pytest.mark.parametrize(
    ("statement", "table"),
    [
        ("UPDATE filter_settings SET revision = 7", "filter_settings"),
        ("UPDATE filter_settings SET default_mode = 'copy'", "filter_settings"),
        (
            "UPDATE mcf_severity_defaults SET severity = 'high' WHERE position = 0",
            "mcf_severity_defaults",
        ),
        ("UPDATE media_access_scopes SET all_sources = 0", "media_access_scopes"),
        (
            "INSERT INTO community_identities (credential_ref, created_at) "
            "VALUES (lower(hex(randomblob(16))), CURRENT_TIMESTAMP)",
            "community_identities",
        ),
        ("DELETE FROM community_identities", "community_identities"),
    ],
)
def test_customized_seeds_are_work(tmp_path, statement, table):
    database = tmp_path / "scanner.db"
    migrate_fresh_database(database, tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(statement)
    before = _files(tmp_path)
    with pytest.raises(RestoreRefused) as refused:
        require_empty_installation(tmp_path, database)
    assert refused.value.reason == HAS_WORK
    assert str(refused.value) == f"This installation already has data in {table}"
    assert _files(tmp_path) == before


@pytest.mark.parametrize(
    "damage",
    ["unknown_revision", "extra_table", "missing_seed", "zero_tables", "not_sqlite"],
)
def test_an_unsupported_database_is_refused_unchanged(tmp_path, damage):
    database = tmp_path / "scanner.db"
    if damage == "zero_tables":
        database.write_bytes(b"")
    elif damage == "not_sqlite":
        database.write_bytes(b"not a database" * 100)
    else:
        migrate_fresh_database(database, tmp_path)
        with sqlite3.connect(database) as connection:
            connection.execute(
                {
                    "unknown_revision": "UPDATE alembic_version "
                    "SET version_num = '9999_unknown_schema'",
                    "extra_table": "CREATE TABLE stray (id INTEGER)",
                    "missing_seed": "DELETE FROM mcf_severity_defaults WHERE position = 0",
                }[damage]
            )
    before = _files(tmp_path)
    with pytest.raises(RestoreRefused) as refused:
        require_empty_installation(tmp_path, database)
    assert refused.value.reason == UNSUPPORTED_SCHEMA
    assert _files(tmp_path) == before


def test_activation_refuses_a_schema_changed_after_preview(tmp_path):
    archive, target = _fixture(tmp_path)
    with preview_restore(archive, target) as preview:
        with sqlite3.connect(target / "scanner.db") as connection:
            connection.execute("UPDATE alembic_version SET version_num = '9999_unknown_schema'")
        before = _files(target)
        with pytest.raises(RestoreRefused) as refused:
            activate_restore(preview, target / "scanner.db", target, confirmed=True)
    assert refused.value.reason == UNSUPPORTED_SCHEMA
    assert _files(target) == before


def test_an_interrupted_restore_into_an_older_empty_installation_rolls_back(tmp_path):
    archive, target = _fixture(tmp_path)
    (target / "scanner.db").unlink()
    _migrated(target / "scanner.db", adoption.COMMUNITY_IDENTITY_REVISION)
    original = _dump(target / "scanner.db")
    script = (
        "import os, sys; from pathlib import Path; "
        "from scanner.backup import activation as a; "
        "from scanner.backup.restore import preview_restore; "
        "original = a._apply; "
        "a._apply = lambda *args: (original(*args), os._exit(42)); " + _ACTIVATE
    )
    result = subprocess.run([sys.executable, "-c", script, str(archive), str(target)], check=False)
    assert result.returncode == 42
    assert _dump(target / "scanner.db") != original
    assert recover_restore(target / "scanner.db", target)
    assert _dump(target / "scanner.db") == original
    _restore(archive, target)


def _dump(path):
    with closing(sqlite3.connect(path)) as connection:
        return list(connection.iterdump())


def _bind(directory, payload):
    path = directory / "deployment.json"
    path.write_text(json.dumps(payload))
    os.chmod(path, 0o600)
    return path.read_bytes()


@pytest.mark.parametrize("target_bound", [True, False])
def test_the_empty_installation_keeps_its_own_binding(tmp_path, target_bound):
    _archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    _bind(source, {"version": 1, "base_url": "https://source.example/editarr", "port": 9443})
    archive = tmp_path / "bound.backup"
    export_backup(source / "scanner.db", source, archive)
    own = _bind(target, {"version": 1, "port": 9201}) if target_bound else None
    expected = {"base_url": "https://source.example/editarr", "port": 9443}
    with preview_restore(archive, target) as preview:
        assert preview.plan["backup_deployment"] == expected
        report = activate_restore(preview, target / "scanner.db", target, confirmed=True)
    assert report["backup_deployment"] == expected
    assert json.loads((target / REPORT).read_text())["backup_deployment"] == expected
    if target_bound:
        assert (target / "deployment.json").read_bytes() == own
    else:
        assert not (target / "deployment.json").exists()


@pytest.mark.parametrize("content", ['{"version": 2}', "not json", '{"version": 1, "port": 0}'])
def test_an_invalid_backup_binding_is_refused(tmp_path, content):
    (tmp_path / "deployment.json").write_text(content)
    with pytest.raises(BackupError, match="deployment configuration"):
        backup_deployment(tmp_path)
