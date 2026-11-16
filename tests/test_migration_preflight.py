"""Startup and offline migration admission against disposable SQLite files."""

import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from scanner import cli, worker
from scanner.commands.migration import run_migration_command
from scanner.http import api_lifecycle
from scanner.persistence.migrations import adoption, preflight
from scanner.settings.config import Settings
from tests.community_schema_fixture import create_unversioned_fixture


@pytest.fixture
def configured(monkeypatch, tmp_path):
    configuration = Settings(data_dir=tmp_path)
    monkeypatch.setattr(preflight, "settings", configuration)
    return tmp_path / "scanner.db", tmp_path


def test_fresh_preflight_creates_versioned_schema(configured):
    path, _data_dir = configured
    assert run_migration_command("inspect").endswith("Schema: missing")
    assert preflight.prepare_database() == adoption.HEAD_REVISION
    assert preflight.require_supported_schema() == adoption.HEAD_REVISION
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT version_num FROM alembic_version").fetchone() == (
            adoption.HEAD_REVISION,
        )


@pytest.mark.parametrize(
    ("after_retry", "expected"),
    [(adoption.HEAD_REVISION, adoption.HEAD_REVISION), ("0001_baseline", None)],
)
def test_preflight_upgrades_again_and_rechecks_a_behind_schema(
    configured, monkeypatch, after_retry, expected
):
    path, _data_dir = configured
    path.touch()
    revisions = iter(["0001_baseline", after_retry])
    upgrades = []
    monkeypatch.setattr(preflight, "upgrade_existing_database", lambda *args: upgrades.append(args))
    monkeypatch.setattr(preflight, "inspect_schema", lambda *_args: next(revisions))

    if expected is None:
        with pytest.raises(RuntimeError, match="0001_baseline"):
            preflight.prepare_database()
    else:
        assert preflight.prepare_database() == expected
    assert len(upgrades) == 2


def test_unversioned_startup_snapshots_and_adopts_before_queries(configured):
    path, data_dir = configured
    create_unversioned_fixture(path).dispose()
    assert run_migration_command("inspect").endswith("Schema: unversioned-v1")
    with pytest.raises(RuntimeError, match="unversioned-v1"):
        preflight.require_supported_schema()
    assert run_migration_command("upgrade").endswith(f"Schema: {adoption.HEAD_REVISION}")
    assert (data_dir / adoption.JOURNAL_NAME).exists()
    assert preflight.require_supported_schema() == adoption.HEAD_REVISION


@pytest.mark.parametrize("revision", ["future", "failed"])
def test_unsupported_or_failed_startup_does_not_write(configured, revision):
    path, data_dir = configured
    create_unversioned_fixture(path).dispose()
    if revision == "future":
        with sqlite3.connect(path) as database:
            database.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
            database.execute("INSERT INTO alembic_version VALUES ('future')")
    else:
        (data_dir / adoption.JOURNAL_NAME).write_text(
            json.dumps({"database": str(path.resolve()), "status": "failed"})
        )
    original = path.read_bytes()
    with pytest.raises((RuntimeError, ValueError), match="[Uu]nsupported|unresolved"):
        preflight.prepare_database()
    assert path.read_bytes() == original
    assert not list((data_dir / "migration-snapshots").glob("snapshot-*"))


def test_unresolved_journal_blocks_missing_file(configured):
    path, data_dir = configured
    (data_dir / adoption.JOURNAL_NAME).write_text(
        json.dumps({"database": str(path.resolve()), "status": "intent"})
    )
    with pytest.raises(RuntimeError, match="unresolved"):
        preflight.prepare_database()
    assert not path.exists()


def test_symlink_database_is_rejected_without_mutating_target(configured, tmp_path):
    path, _data_dir = configured
    target = tmp_path / "target.db"
    create_unversioned_fixture(target).dispose()
    original = target.read_bytes()
    path.symlink_to(target)
    with pytest.raises(ValueError, match="regular SQLite database"):
        preflight.prepare_database()
    assert target.read_bytes() == original


def test_worker_rejects_unversioned_file_before_queue_access(configured, monkeypatch):
    path, data_dir = configured
    create_unversioned_fixture(path).dispose()
    monkeypatch.setattr(worker, "settings", SimpleNamespace(data_dir=data_dir))
    monkeypatch.setattr(worker, "_run_scan_worker", lambda *_args: pytest.fail("queue queried"))
    with pytest.raises(RuntimeError, match="unversioned-v1"):
        worker.run_scan_worker("scan", "token")
    assert adoption._inspect_database(path) is None


def test_cli_inspection_and_failed_upgrade_are_actionable(configured, monkeypatch, capsys):
    path, _data_dir = configured
    create_unversioned_fixture(path).dispose()
    monkeypatch.setattr(sys, "argv", ["editarr", "migration", "inspect"])
    cli.main()
    assert capsys.readouterr().out.endswith("Schema: unversioned-v1\n")
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        database.execute("INSERT INTO alembic_version VALUES ('future')")
    monkeypatch.setattr(sys, "argv", ["editarr", "migration", "upgrade"])
    with pytest.raises(SystemExit) as failure:
        cli.main()
    assert failure.value.code == 1
    assert "Unsupported database revision" in capsys.readouterr().err


def test_http_preflight_precedes_auth_state(configured, monkeypatch):
    from scanner import api

    monkeypatch.setattr(api, "active_deployment_snapshot", lambda: None)
    monkeypatch.setattr(api, "_secure_data_directory", lambda: None)
    monkeypatch.setattr(api, "ensure_user_private_directory", lambda *_args: None)
    monkeypatch.setattr(
        api, "load_diagnostics_preferences", lambda *_args: SimpleNamespace(debug_enabled=False)
    )
    monkeypatch.setattr(api, "configure_runtime_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        api, "init_db", lambda: (_ for _ in ()).throw(RuntimeError("preflight denied"))
    )
    monkeypatch.setattr(
        api, "load_csrf_encryption_key", lambda *_args: pytest.fail("auth state written")
    )
    with pytest.raises(RuntimeError, match="preflight denied"):
        api_lifecycle.startup()


def test_recovery_export_requires_separate_directory(configured, tmp_path):
    path, data_dir = configured
    create_unversioned_fixture(path).dispose()
    preflight.prepare_database()
    with pytest.raises(ValueError, match="outside"):
        run_migration_command("recover-export", str(data_dir / "copy.db"))
    recovery_dir = tmp_path.parent / f"{tmp_path.name}-recovery"
    recovery_dir.mkdir()
    exported = recovery_dir / "copy.db"
    assert str(exported) in run_migration_command("recover-export", str(exported))
    assert adoption._inspect_database(exported) is None
