"""MU-06: crash-safe identity moves, unshared credential references, audit rows, and backups.

Interrupted moves run in a child process that exits hard at the move. Nothing here
contacts a community service.
"""

import json
import socket
import sqlite3
import subprocess
import sys
import tarfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scanner.backup.activation import activate_restore
from scanner.backup.archive import export_backup
from scanner.backup.restore import preview_restore
from scanner.backup.restore_target import RestoreRefused
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.persistence.repository.repository_community import adopt_owner_identity
from scanner.security.community_identity import (
    holds_identity,
    load_or_create_installation_identity,
)
from scanner.security.community_identity_homes import owner_home
from scanner.security.private_identity_files import (
    DIRECTORY,
    HOMES,
    IdentityHome,
    IdentityUnavailable,
)
from tests.activity_migration_support import link_owner
from tests.community_identity_migration_support import (
    PERSONAL_FILTERS,
    build_community_database,
    identity_files,
)
from tests.community_identity_migration_support import no_network as no_network

_OWNER = (
    "INSERT INTO users (id, role, status, username, normalized_username, local_export_id, "
    "permission_revision, created_at, updated_at) VALUES (?, ?, 'active', ?, ?, ?, 1, "
    "'2026-10-21', '2026-10-21')"
)


def _resolve(path):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with sessionmaker(bind=engine)() as db:
            adopt_owner_identity(db, path.parent)
    finally:
        engine.dispose()
    return owner_home(path.parent, path)


def _homes(data_dir):
    root = data_dir / HOMES
    return sorted(entry.name for entry in root.iterdir() if holds_identity(entry))


def _upgraded(tmp_path):
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700, parents=True)
    files = build_community_database(path, tmp_path / "media", PERSONAL_FILTERS)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        (reference,) = database.execute(
            "SELECT credential_ref FROM community_identities"
        ).fetchone()
    return path, files, IdentityHome(path.parent, reference)


def _resolve_in_child(path, patch, code):
    script = (
        "import os, sys; from pathlib import Path; "
        "from sqlalchemy import create_engine; from sqlalchemy.orm import Session; "
        "from scanner.security import community_identity_homes as homes; "
        "from scanner.persistence.repository.repository_community import adopt_owner_identity; "
        f"{patch}; "
        "path = Path(sys.argv[1]); "
        "adopt_owner_identity(Session(create_engine(f'sqlite:///{path}')), path.parent)"
    )
    result = subprocess.run([sys.executable, "-c", script, str(path)], check=False)
    assert result.returncode == code


@pytest.mark.parametrize(
    ("patch", "moved"),
    [
        ("os.rename = lambda *_: os._exit(95)", False),
        ("homes._sync_directory = lambda _path: os._exit(95)", True),
    ],
    ids=["before-rename", "after-rename"],
)
def test_an_interrupted_move_keeps_one_identity_and_finishes_on_restart(tmp_path, patch, moved):
    path, files, home = _upgraded(tmp_path)
    incoming = path.parent / DIRECTORY

    _resolve_in_child(path, patch, 95)

    assert holds_identity(incoming) is not moved
    assert identity_files(home.root if moved else incoming) == files
    assert (not home.root.exists() or not holds_identity(home.root)) is not moved
    assert _resolve(path) == home
    assert not incoming.exists()
    assert identity_files(home.root) == files
    assert _homes(path.parent) == [home.credential_ref]
    original = json.loads(files["identity.json"])
    restarted = load_or_create_installation_identity(home)
    assert (restarted.installation_id, restarted.key_id) == (
        original["installation_id"],
        original["key_id"],
    )
    assert identity_files(home.root) == files


def test_no_identity_is_created_while_incoming_files_wait(tmp_path):
    path, files, home = _upgraded(tmp_path)
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(home)
    assert error.value.status == "pending"
    assert not holds_identity(home.root)
    assert identity_files(path.parent / DIRECTORY) == files


def test_two_identity_locations_are_refused_without_changing_either(tmp_path):
    source, _, _ = _upgraded(tmp_path / "source")
    archive = tmp_path / "portable.backup"
    export_backup(source, source.parent, archive)
    path, files, home = _upgraded(tmp_path / "target")
    incoming = path.parent / DIRECTORY
    incoming.rename(path.parent / "secrets" / "held")
    other = load_or_create_installation_identity(home)
    (path.parent / "secrets" / "held").rename(incoming)
    in_home = identity_files(home.root)

    with pytest.raises(IdentityUnavailable) as error:
        _resolve(path)
    with pytest.raises(IdentityUnavailable, match="Two locations"):
        export_backup(path, path.parent, tmp_path / "refused.backup")
    with pytest.raises(RestoreRefused):
        with preview_restore(archive, path.parent):
            pass

    assert error.value.status == "conflict"
    assert identity_files(incoming) == files
    assert identity_files(home.root) == in_home
    assert other.installation_id != json.loads(files["identity.json"])["installation_id"]


def test_a_home_with_only_lock_files_is_replaced_by_the_incoming_identity(tmp_path):
    path, files, home = _upgraded(tmp_path)
    home.root.mkdir(mode=0o700, parents=True)
    (home.root / "identity.lock").write_bytes(b"\0")
    (home.root / "rotation.lock").write_bytes(b"\0")
    assert _resolve(path) == home
    assert identity_files(home.root) == files


@pytest.mark.parametrize("reference", ["../../escape", "F" * 32, "f" * 31, "g" * 32, ""])
def test_credential_references_are_lowercase_hex_names_only(tmp_path, reference):
    with pytest.raises(ValueError):
        IdentityHome(tmp_path, reference)


def test_accounts_never_share_a_credential_reference_or_a_home(tmp_path):
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700, parents=True)
    migrate_fresh_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute(_OWNER, (1, "owner", "owner", "owner", "local-" + "1" * 32))
        database.execute(_OWNER, (2, "user", "user", "user", "local-" + "2" * 32))
        (first,) = database.execute("SELECT credential_ref FROM community_identities").fetchone()
        database.execute("UPDATE community_identities SET user_id = 1")
        second = "0" * 32
        database.execute(
            "INSERT INTO community_identities VALUES (?, 2, '2026-10-21 00:00:00.000000')",
            (second,),
        )
        for statement, values in (
            ("INSERT INTO community_identities VALUES (?, NULL, '2026-10-21')", (first,)),
            ("INSERT INTO community_identities VALUES (?, 1, '2026-10-21')", ("1" * 32,)),
            ("INSERT INTO community_identities VALUES (?, NULL, '2026-10-21')", ("A" * 32,)),
            ("INSERT INTO community_identities VALUES (?, NULL, '2026-10-21')", ("a" * 31,)),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(statement, values)
    owner, user = IdentityHome(path.parent, first), IdentityHome(path.parent, second)
    assert owner.root != user.root
    identities = [load_or_create_installation_identity(home) for home in (owner, user)]
    assert identities[0].installation_id != identities[1].installation_id
    assert _resolve(path) == owner
    assert load_or_create_installation_identity(owner).key_id == identities[0].key_id


def test_audit_rows_name_an_account_only_for_user_actors(tmp_path):
    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    insert = (
        "INSERT INTO audit_events (event_id, occurred_at, action, outcome, actor_kind, actor_id, "
        "credential, details_json) VALUES (?, '2026-10-21 00:00:00', ?, ?, ?, ?, ?, ?)"
    )
    event = iter(f"00000000-0000-4000-8000-{index:012d}" for index in range(100))
    with sqlite3.connect(path) as database:
        database.execute(_OWNER, (1, "owner", "owner", "owner", "local-" + "1" * 32))
        legacy = json.dumps({"legacy_actor": "administrator", "legacy_admin_user_id": 7})
        for row in (
            ("auth.login", "succeeded", "user", 1, "session", "{}"),
            ("auth.audit_started", "succeeded", "system", None, None, "{}"),
            ("auth.login", "failed", "anonymous", None, None, "{}"),
            ("auth.login", "succeeded", "unattributed", None, None, legacy),
        ):
            database.execute(insert, (next(event), *row))
        for row in (
            ("auth.login", "succeeded", "user", None, None, "{}"),
            ("auth.login", "succeeded", "unattributed", 1, None, legacy),
            ("auth.login", "succeeded", "system", None, "session", "{}"),
            ("auth.login", "succeeded", "user", 1, "password", "{}"),
            ("auth.login", "ok", "system", None, None, "{}"),
            ("", "succeeded", "system", None, None, "{}"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(insert, (next(event), *row))
        assert database.execute(
            "SELECT actor_kind, actor_id FROM audit_events ORDER BY id"
        ).fetchall() == [("user", 1), ("system", None), ("anonymous", None), ("unattributed", None)]


def test_these_tests_cannot_open_a_network_connection():
    with pytest.raises(AssertionError, match="network"):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_backup_carries_the_owner_identity_and_restore_moves_it_into_the_restored_home(tmp_path):
    source, files, home = _upgraded(tmp_path / "source")
    assert _resolve(source) == home
    link_owner(source)
    archive = tmp_path / "portable.backup"
    export_backup(source, source.parent, archive)
    with tarfile.open(archive) as members:
        names = set(members.getnames())
        manifest = json.load(members.extractfile("manifest.json"))
        archived = {name: members.extractfile(f"secrets/community/{name}").read() for name in files}
    assert {f"secrets/community/{name}" for name in files} <= names
    assert archived == files
    assert manifest["identity_key_id"] == json.loads(files["identity.json"])["key_id"]

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    migrate_fresh_database(target / "scanner.db", target)
    with preview_restore(archive, target) as preview:
        activate_restore(preview, target / "scanner.db", target, confirmed=True)
    assert identity_files(target / DIRECTORY) == files

    restored = _resolve(target / "scanner.db")

    assert restored.credential_ref == home.credential_ref
    assert identity_files(restored.root) == files
    assert _homes(target) == [home.credential_ref]
