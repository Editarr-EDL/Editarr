"""MU-03: revisions 0011 and 0012 keep existing activity, owned or awaiting owner setup."""

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scanner.integrations.jellyfin_setup import JellyfinSetupClient, SetupRefused
from scanner.persistence.migrations import adoption, preflight
from scanner.persistence.repository.activity_ownership import (
    activity_owner,
    assign_awaiting_activity,
)
from scanner.security.jellyfin_accounts.owner_setup import set_up_owner
from tests.activity_migration_support import (
    ACCOUNTS,
    ACTIVITY,
    SUPPORTED,
    assert_assigned,
    build_activity_database,
    rebind_to_jellyfin,
    sidecar_state,
)
from tests.jellyfin_setup_fake import ADMIN, ADMIN_PASSWORD, SERVER_URL, FakeJellyfin
from tests.test_account_migration import _snapshot


@pytest.fixture
def no_processes(monkeypatch):
    """Fail if anything tries to start a worker or downloader process."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("migration started a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)


def _built(tmp_path, revision, *, administrator=True):
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir()
    _cookie, sidecars = build_activity_database(
        path, tmp_path / "media", revision, administrator=administrator
    )
    rebind_to_jellyfin(path)
    return path, sidecars


@pytest.mark.parametrize("revision", SUPPORTED)
def test_every_supported_revision_assigns_activity_to_the_owner_once(
    tmp_path, revision, no_processes
):
    path, sidecars = _built(tmp_path, revision)
    original, columns = _snapshot(path)
    data_dir = path.parent

    generation = adoption.upgrade_existing_database(path, data_dir)

    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert_assigned(path, original, columns, 1, source_revision=revision)
    assert sidecar_state(tmp_path / "media") == sidecars
    assert {entry.name for entry in data_dir.iterdir()} == {
        adoption.JOURNAL_NAME,
        "maintenance",
        "migration-snapshots",
        "scanner.db",
    }
    assert adoption.upgrade_existing_database(path, data_dir) is None
    assert_assigned(path, original, columns, 1, source_revision=revision)
    recovered = adoption.export_upgrade_recovery(path, data_dir, tmp_path / "recovered.db")
    assert generation.is_dir()
    assert adoption._inspect_database(recovered) == revision
    assert _snapshot(recovered)[0] == original


def _owner_setup(path, attempts=1):
    """Race Jellyfin owner setups against one database; return the winning user IDs."""
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine)
    jellyfin = FakeJellyfin()
    gate = Barrier(attempts)

    def setup(_attempt):
        gate.wait()
        with (
            factory() as db,
            JellyfinSetupClient(SERVER_URL, transport=jellyfin.transport()) as client,
        ):
            try:
                return set_up_owner(
                    db,
                    client,
                    ADMIN,
                    ADMIN_PASSWORD,
                    clock=lambda: datetime.now(timezone.utc),
                    issue=lambda user_id: user_id,
                )
            except SetupRefused:
                return None

    try:
        with ThreadPoolExecutor(max_workers=attempts) as pool:
            return list(pool.map(setup, range(attempts))), factory, engine
    except BaseException:
        engine.dispose()
        raise


@pytest.mark.parametrize("revision", [None, "0009_readable_api_key", ACCOUNTS, ACTIVITY])
def test_activity_without_an_owner_waits_then_owner_setup_assigns_it_once(
    tmp_path, revision, monkeypatch, no_processes
):
    path, sidecars = _built(tmp_path, revision, administrator=False)
    original, columns = _snapshot(path)
    assert adoption.upgrade_existing_database(path, path.parent).is_dir()
    assert_assigned(path, original, columns, None, source_revision=revision)
    monkeypatch.setattr(preflight, "database_location", lambda: (path, path.parent))
    with pytest.raises(RuntimeError, match="Owner setup is required"):
        preflight.require_owner_setup()

    winners, factory, engine = _owner_setup(path, 2)
    try:
        assert sorted(winners, key=str) == [1, None]
        assert_assigned(path, original, columns, 1, source_revision=revision, after_setup=True)
        before = _snapshot(path)[0]
        with factory() as db:
            owner = activity_owner(db)
            assert (owner.user_id, owner.permission_revision) == (1, 1)
            assign_awaiting_activity(db, owner)
            db.commit()
        assert _snapshot(path)[0] == before
    finally:
        engine.dispose()
    preflight.require_owner_setup()
    assert sidecar_state(tmp_path / "media") == sidecars


def _assert_recoverable(path, original, revision):
    data_dir = path.parent
    with pytest.raises(RuntimeError, match="unresolved"):
        preflight.inspect_schema(path, data_dir)
    with pytest.raises(RuntimeError, match="unresolved"):
        adoption.upgrade_existing_database(path, data_dir)
    recovery = data_dir.parent / "recovery"
    recovery.mkdir()
    recovered = adoption.export_upgrade_recovery(path, data_dir, recovery / "scanner.db")
    assert adoption._inspect_database(recovered) == revision
    assert _snapshot(recovered)[0] == original
    assert adoption.upgrade_existing_database(recovered, recovery).is_dir()
    assert adoption._inspect_database(recovered) == adoption.HEAD_REVISION


def test_failure_after_the_export_rebuild_blocks_admission_and_recovers(tmp_path, monkeypatch):
    import alembic.op

    path, sidecars = _built(tmp_path, ACCOUNTS)
    original, columns = _snapshot(path)
    real_create_index = alembic.op.create_index

    def fail_after_rebuild(name, *args, **kwargs):
        if name == "uq_media_item_exports_shared":
            raise RuntimeError("injected 0011 failure")
        return real_create_index(name, *args, **kwargs)

    monkeypatch.setattr(alembic.op, "create_index", fail_after_rebuild)
    with pytest.raises(RuntimeError, match="injected 0011 failure"):
        adoption.upgrade_existing_database(path, path.parent)
    monkeypatch.setattr(alembic.op, "create_index", real_create_index)
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert (journal["status"], journal["from_revision"]) == ("failed", ACCOUNTS)
    _assert_recoverable(path, original, ACCOUNTS)
    assert_assigned(
        path.parent.parent / "recovery" / "scanner.db",
        original,
        columns,
        1,
        source_revision=ACCOUNTS,
    )
    assert sidecar_state(tmp_path / "media") == sidecars


def test_hard_exit_inside_0011_blocks_admission_and_recovers(tmp_path):
    path, sidecars = _built(tmp_path, "0009_readable_api_key")
    original, _ = _snapshot(path)
    script = (
        "import os, sys, alembic.op; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        "real = alembic.op.create_index; "
        "alembic.op.create_index = lambda name, *a, **k: os._exit(91) "
        "if name == 'uq_media_item_exports_personal' else real(name, *a, **k); "
        "adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(path.parent)], check=False
    )
    assert result.returncode == 91
    assert json.loads((path.parent / adoption.JOURNAL_NAME).read_text())["status"] == "intent"
    _assert_recoverable(path, original, "0009_readable_api_key")
    assert sidecar_state(tmp_path / "media") == sidecars


def test_failure_inside_0012_blocks_admission_and_recovers(tmp_path, monkeypatch):
    import alembic.op

    path, sidecars = _built(tmp_path, ACTIVITY)
    original, _ = _snapshot(path)
    real_create_table = alembic.op.create_table

    def fail_late(name, *args, **kwargs):
        if name == "user_filter_severity_overrides":
            raise RuntimeError("injected 0012 failure")
        return real_create_table(name, *args, **kwargs)

    monkeypatch.setattr(alembic.op, "create_table", fail_late)
    with pytest.raises(RuntimeError, match="injected 0012 failure"):
        adoption.upgrade_existing_database(path, path.parent)
    monkeypatch.setattr(alembic.op, "create_table", real_create_table)
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert (journal["status"], journal["from_revision"]) == ("failed", ACTIVITY)
    _assert_recoverable(path, original, ACTIVITY)
    assert sidecar_state(tmp_path / "media") == sidecars


def test_hard_exit_inside_0012_blocks_admission_and_recovers(tmp_path):
    path, sidecars = _built(tmp_path, ACTIVITY)
    original, _ = _snapshot(path)
    script = (
        "import os, sys, alembic.op; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        "real = alembic.op.create_table; "
        "alembic.op.create_table = lambda name, *a, **k: os._exit(92) "
        "if name == 'user_filter_copy_severities' else real(name, *a, **k); "
        "adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(path.parent)], check=False
    )
    assert result.returncode == 92
    assert json.loads((path.parent / adoption.JOURNAL_NAME).read_text())["status"] == "intent"
    _assert_recoverable(path, original, ACTIVITY)
    assert sidecar_state(tmp_path / "media") == sidecars


@pytest.mark.parametrize(
    ("revision", "mutation"),
    [
        (ACCOUNTS, "ALTER TABLE saved_reviews ADD COLUMN owner_id INTEGER"),
        (ACCOUNTS, "ALTER TABLE media_item_exports ADD COLUMN scope VARCHAR(8)"),
        (ACCOUNTS, "CREATE TABLE user_filter_preferences (user_id INTEGER PRIMARY KEY)"),
        (ACTIVITY, "CREATE TABLE user_filter_copy_rules (user_id INTEGER, id VARCHAR)"),
        (ACTIVITY, "CREATE TABLE user_filter_severity_overrides (user_id INTEGER)"),
    ],
)
def test_partially_applied_shapes_are_refused_without_snapshot(tmp_path, revision, mutation):
    path, _ = _built(tmp_path, revision)
    with sqlite3.connect(path) as database:
        database.execute(mutation)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption.upgrade_existing_database(path, path.parent)
    assert path.read_bytes() == before
    assert not (path.parent / adoption.JOURNAL_NAME).exists()
    assert not (path.parent / "migration-snapshots").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "DROP INDEX uq_media_item_exports_shared",
        "DROP TABLE user_filter_severity_overrides",
    ],
)
def test_a_modified_head_schema_is_refused(tmp_path, mutation):
    path, _ = _built(tmp_path, ACCOUNTS)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute(mutation)
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption._inspect_database(path)


def _archive_with_database(archive, database, revision, output):
    """Replace the database member and schema revision of a real backup archive."""
    payload = database.read_bytes()
    with tarfile.open(archive, "r:") as original, tarfile.open(output, "w:") as rewritten:
        manifest = json.load(original.extractfile("manifest.json"))
        manifest["schema_revision"] = revision
        manifest["files"]["database.sqlite3"] = {
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        members = {"manifest.json": json.dumps(manifest).encode(), "database.sqlite3": payload}
        for member in original:
            members.setdefault(member.name, original.extractfile(member).read())
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            rewritten.addfile(info, io.BytesIO(content))
