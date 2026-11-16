"""MU-06: revision 0013 gives the owner the existing community identity and history.

Covers every supported source, interrupted and refused upgrades, the crash-safe move
of private files into the owner's home, unshared credential references, unattributed
legacy history, and backups. Nothing here contacts a community service.
"""

import json
import shutil
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scanner.persistence.migrations import adoption
from scanner.persistence.repository.repository_community import (
    load_local_identity,
)
from scanner.security.community_identity import load_or_create_installation_identity
from scanner.security.private_identity_files import DIRECTORY, HOMES, IdentityHome
from tests.activity_migration_support import ACCOUNTS, ACTIVITY, rebind_to_jellyfin
from tests.community_identity_migration_support import (
    COMMUNITY_TABLES,
    CONTRIBUTOR,
    PERSONAL_FILTERS,
    SOURCES,
    build_community_database,
    identity_files,
)
from tests.community_identity_migration_support import no_network as no_network
from tests.community_identity_support import client_home
from tests.test_account_migration import _snapshot
from tests.test_activity_migration import _assert_recoverable, _owner_setup

_COMMUNITY_SOURCES = SOURCES[SOURCES.index("0003_local_community") :]


def _built(tmp_path, revision, *, administrator=True):
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700, parents=True)
    files = build_community_database(
        path, tmp_path / "media", revision, administrator=administrator
    )
    rebind_to_jellyfin(path)
    return path, files


def _rows(path, table, columns):
    with sqlite3.connect(path) as database:
        selected = ", ".join(f'"{name}"' for name in columns)
        return database.execute(f'SELECT {selected} FROM "{table}" ORDER BY rowid').fetchall()


def _resolve(path):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with sessionmaker(bind=engine)() as db:
            home = client_home(db, path.parent)
            return home, load_local_identity(db, home, recovery=True)
    finally:
        engine.dispose()


def _homes(data_dir):
    root = data_dir / HOMES
    return sorted(
        entry.name
        for entry in (root.iterdir() if root.is_dir() else ())
        if entry.is_dir() and identity_files(entry)
    )


@pytest.mark.parametrize("revision", SOURCES)
def test_every_supported_revision_gives_the_owner_its_identity_and_history(tmp_path, revision):
    path, files = _built(tmp_path, revision)
    original, columns = _snapshot(path)
    audit_log = (tmp_path / "logs" / "auth-audit.jsonl").read_bytes()
    recovery = json.loads(files["identity.json"])["recovery"]

    generation = adoption.upgrade_existing_database(path, path.parent)

    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    with sqlite3.connect(path) as database:
        identities = database.execute(
            "SELECT credential_ref, user_id FROM community_identities"
        ).fetchall()
        assert [user for _ref, user in identities] == [1]
        reference = identities[0][0]
        assert len(reference) == 32 and set(reference) <= set("0123456789abcdef")
        assert database.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
    assert identity_files(path.parent / DIRECTORY) == files, "the migration moves no file"
    for table in COMMUNITY_TABLES:
        if table in original:
            assert _rows(path, table, columns[table]) == original[table], table
    if revision in _COMMUNITY_SOURCES:
        assert _rows(path, "community_connections", ["credential_ref"]) == [(reference,)]
        assert _rows(path, "community_operations", ["operation_id", "owner_id", "actor_id"]) == [
            ("op-receipt", 1, None),
            ("op-vote", 1, None),
            ("op-publish", 1, None),
        ]

    home, identity = _resolve(path)

    assert home.credential_ref == reference
    assert not (path.parent / DIRECTORY).exists()
    assert identity_files(home.root) == files
    assert _homes(path.parent) == [reference]
    assert identity.recovery == recovery
    assert identity.contributor_id == "contributor-0001"
    assert (tmp_path / "logs" / "auth-audit.jsonl").read_bytes() == audit_log
    assert adoption.upgrade_existing_database(path, path.parent) is None
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert generation.is_dir()
    assert adoption._inspect_database(recovered) == revision
    assert _snapshot(recovered)[0] == original


@pytest.mark.parametrize("revision", ["0007_community_publications", ACCOUNTS, PERSONAL_FILTERS])
def test_history_without_an_owner_waits_and_owner_setup_never_invents_an_actor(tmp_path, revision):
    path, files = _built(tmp_path, revision, administrator=False)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT user_id FROM community_identities").fetchall() == [(None,)]
    home, identity = _resolve(path)
    assert identity_files(home.root) == files
    assert _rows(path, "community_operations", ["owner_id", "actor_id"]) == [(None, None)] * 3

    winners, _factory, engine = _owner_setup(path, 2)
    engine.dispose()

    assert sorted(winners, key=str) == [1, None]
    with sqlite3.connect(path) as database:
        assert database.execute(
            "SELECT credential_ref, user_id FROM community_identities"
        ).fetchall() == [(home.credential_ref, 1)]
    assert _rows(path, "community_operations", ["owner_id", "actor_id"]) == [(1, None)] * 3
    assert _resolve(path)[0] == home


def test_failure_inside_0013_blocks_admission_and_recovers(tmp_path, monkeypatch):
    import alembic.op

    path, files = _built(tmp_path, PERSONAL_FILTERS)
    original, _ = _snapshot(path)
    real_create_table = alembic.op.create_table

    def fail_late(name, *args, **kwargs):
        if name == "audit_events":
            raise RuntimeError("injected 0013 failure")
        return real_create_table(name, *args, **kwargs)

    monkeypatch.setattr(alembic.op, "create_table", fail_late)
    with pytest.raises(RuntimeError, match="injected 0013 failure"):
        adoption.upgrade_existing_database(path, path.parent)
    monkeypatch.setattr(alembic.op, "create_table", real_create_table)
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert (journal["status"], journal["from_revision"]) == ("failed", PERSONAL_FILTERS)
    _assert_recoverable(path, original, PERSONAL_FILTERS)
    assert identity_files(path.parent / DIRECTORY) == files


def test_hard_exit_inside_0013_blocks_admission_and_recovers(tmp_path):
    path, files = _built(tmp_path, ACTIVITY)
    original, _ = _snapshot(path)
    script = (
        "import os, sys, alembic.op; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        "real = alembic.op.create_index; "
        "alembic.op.create_index = lambda name, *a, **k: os._exit(93) "
        "if name == 'ix_community_operations_owner_id' else real(name, *a, **k); "
        "adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(path.parent)], check=False
    )
    assert result.returncode == 93
    assert json.loads((path.parent / adoption.JOURNAL_NAME).read_text())["status"] == "intent"
    _assert_recoverable(path, original, ACTIVITY)
    assert identity_files(path.parent / DIRECTORY) == files


@pytest.mark.parametrize(
    "mutation",
    [
        "CREATE TABLE community_identities (credential_ref VARCHAR(32) PRIMARY KEY)",
        "ALTER TABLE community_operations ADD COLUMN owner_id INTEGER",
        "CREATE TABLE audit_events (id INTEGER PRIMARY KEY)",
    ],
)
def test_partially_applied_0013_shapes_are_refused_without_snapshot(tmp_path, mutation):
    path, _ = _built(tmp_path, PERSONAL_FILTERS)
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
    ["DROP INDEX ix_audit_events_occurred_at", "DROP INDEX ix_community_operations_owner_id"],
)
def test_a_modified_0013_head_is_refused(tmp_path, mutation):
    path, _ = _built(tmp_path, ACCOUNTS)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA foreign_keys = OFF")
        database.execute(mutation)
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption._inspect_database(path)


def _recovery_dir(tmp_path):
    recovery = tmp_path / "recovery"
    recovery.mkdir(mode=0o700)
    return recovery


@pytest.mark.parametrize("adopted", [False, True], ids=["before-move", "after-move"])
def test_a_recovery_export_reupgrades_with_the_same_identity_and_history(tmp_path, adopted):
    path, files = _built(tmp_path, PERSONAL_FILTERS)
    adoption.upgrade_existing_database(path, path.parent)
    active_root = _resolve(path)[0].root if adopted else path.parent / DIRECTORY
    recovery = _recovery_dir(tmp_path)

    recovered = adoption.export_upgrade_recovery(path, path.parent, recovery / "scanner.db")

    assert identity_files(recovery / DIRECTORY) == files
    shutil.copytree(path.parent / "secrets", recovery / "secrets", dirs_exist_ok=True)
    adoption.upgrade_existing_database(recovered, recovery)
    home, identity = _resolve(recovered)
    assert identity_files(home.root) == files
    assert not (recovery / DIRECTORY).exists()
    assert identity.contributor_id == CONTRIBUTOR
    assert identity.recovery == json.loads(files["identity.json"])["recovery"]
    assert _rows(recovered, "community_operations", ["operation_id", "owner_id"]) == [
        ("op-receipt", 1),
        ("op-vote", 1),
        ("op-publish", 1),
    ]
    assert identity_files(active_root) == files


def test_a_recovery_export_after_a_failure_before_any_home_carries_the_identity(
    tmp_path, monkeypatch
):
    import alembic.op

    path, files = _built(tmp_path, PERSONAL_FILTERS)
    real_create_table = alembic.op.create_table

    def fail_first(name, *args, **kwargs):
        if name == "community_identities":
            raise RuntimeError("injected failure before the identity table")
        return real_create_table(name, *args, **kwargs)

    monkeypatch.setattr(alembic.op, "create_table", fail_first)
    with pytest.raises(RuntimeError, match="injected failure"):
        adoption.upgrade_existing_database(path, path.parent)
    monkeypatch.setattr(alembic.op, "create_table", real_create_table)
    recovery = _recovery_dir(tmp_path)

    recovered = adoption.export_upgrade_recovery(path, path.parent, recovery / "scanner.db")

    assert identity_files(recovery / DIRECTORY) == files
    adoption.upgrade_existing_database(recovered, recovery)
    assert identity_files(_resolve(recovered)[0].root) == files
    assert identity_files(path.parent / DIRECTORY) == files


def test_a_recovery_export_refuses_an_identity_the_generation_never_used(tmp_path):
    path, _ = _built(tmp_path, PERSONAL_FILTERS)
    adoption.upgrade_existing_database(path, path.parent)
    home, _ = _resolve(path)
    other = IdentityHome(tmp_path / "other", "f" * 32)
    load_or_create_installation_identity(other)
    shutil.rmtree(home.root)
    other.root.rename(home.root)
    recovery = _recovery_dir(tmp_path)

    with pytest.raises(ValueError, match="community identity"):
        adoption.export_upgrade_recovery(path, path.parent, recovery / "scanner.db")

    assert list(recovery.iterdir()) == []


@pytest.mark.parametrize(
    "patch",
    [
        "os.rename = lambda *_: os._exit(96)",
        "real = os.rename; os.rename = lambda *a: (real(*a), os._exit(96))",
    ],
    ids=["before-publish", "after-publish"],
)
def test_an_interrupted_recovery_export_retries_to_the_same_identity(tmp_path, patch):
    path, files = _built(tmp_path, PERSONAL_FILTERS)
    adoption.upgrade_existing_database(path, path.parent)
    _resolve(path)
    recovery = _recovery_dir(tmp_path)
    script = (
        "import os, sys; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        f"{patch}; "
        "source = Path(sys.argv[1]); "
        "adoption.export_upgrade_recovery(source, source.parent, Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(recovery / "scanner.db")], check=False
    )
    assert result.returncode == 96
    assert not (recovery / "scanner.db").exists()

    recovered = adoption.export_upgrade_recovery(path, path.parent, recovery / "scanner.db")
    adoption.upgrade_existing_database(recovered, recovery)
    home, _ = _resolve(recovered)

    assert identity_files(home.root) == files
    assert _homes(recovery) == [home.credential_ref]
    assert sorted(entry.name for entry in (recovery / "secrets").iterdir()) == [
        "community-identities"
    ]


def test_each_upgrade_draws_a_fresh_credential_reference(tmp_path):
    references = []
    for name in ("first", "second"):
        path, _ = _built(tmp_path / name, PERSONAL_FILTERS)
        adoption.upgrade_existing_database(path, path.parent)
        references.append(_rows(path, "community_identities", ["credential_ref"])[0][0])
    assert references[0] != references[1]
