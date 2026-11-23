"""MU-13: revision 0015 keeps every key, and the application seals stored plaintext.

Covers every supported source revision, sealing twice, a failed and an
interrupted upgrade, a partly applied or modified shape, an unknown revision,
recovery from the verified pre-upgrade generation, and backups that move the
wrap key with the local accounts. Sources come from `mu06-community-fixture-1`
(through `test_media_access_migration._built`), upgraded to 0014 for the newest.
"""

import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.backup.archive import export_backup
from scanner.persistence.migrations import adoption
from scanner.security.api_key_crypto import WRAP_KEY_FILENAME, load_api_key_wrap_key
from scanner.security.api_keys import (
    authenticate_api_key,
    rotate_api_key,
    seal_database_api_keys,
    seal_plaintext_api_keys,
    stored_api_key,
)
from tests.activity_migration_support import _upgrade
from tests.community_identity_migration_support import no_network as no_network
from tests.explicit_filter_modes_support import drop_0031_additions
from tests.stable_media_identity_migration_support import without_stable_media_identity
from tests.jellyfin_sync_migration_support import drop_0018_additions
from tests.test_account_migration import _snapshot
from tests.test_activity_migration import _assert_recoverable
from tests.test_backup_activation import _fixture, _restore
from tests.test_backup_api import backup_client as backup_client
from tests.test_media_access_migration import IDENTITIES, MIGRATION_SOURCES, _built

MEDIA_ACCESS = "0014_media_access_exceptions"
SOURCES = [*MIGRATION_SOURCES, MEDIA_ACCESS]
KEY = "0123456789abcdef" * 4
DIGEST = hashlib.sha256(KEY.encode()).hexdigest()


def _with_key(tmp_path, revision):
    """A populated database at `revision` whose newest key table holds `KEY` as plaintext."""

    path = _built(tmp_path, IDENTITIES if revision == MEDIA_ACCESS else revision)
    if revision == MEDIA_ACCESS:
        _upgrade(path, MEDIA_ACCESS)
    with sqlite3.connect(path) as database:
        columns = {row[1] for row in database.execute("PRAGMA table_info(api_keys)")}
        if not columns:
            return path, False
        database.execute("DELETE FROM api_keys")
        values = {"id": 1, "token_digest": DIGEST, "created_at": "2026-10-22 12:00:00"}
        if "token_value" in columns:
            values["token_value"] = KEY
        if "user_id" in columns:
            values["user_id"] = database.execute(
                "SELECT id FROM users WHERE role = 'owner'"
            ).fetchone()[0]
        database.execute(
            f"INSERT INTO api_keys ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            tuple(values.values()),
        )
    return path, "token_value" in columns


def _keys(path):
    with sqlite3.connect(path) as database:
        return database.execute(
            "SELECT token_digest, token_value, secret_envelope FROM api_keys ORDER BY id"
        ).fetchall()


def _seal(path):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as db:
            return seal_plaintext_api_keys(db, load_api_key_wrap_key(path.parent))
    finally:
        engine.dispose()


@pytest.mark.parametrize("revision", SOURCES)
def test_every_supported_revision_keeps_its_key_and_sealing_removes_plaintext(tmp_path, revision):
    path, readable = _with_key(tmp_path, revision)
    original, _ = _snapshot(path)

    generation = adoption.upgrade_existing_database(path, path.parent)

    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    if revision == MEDIA_ACCESS:
        upgraded, _ = _snapshot(path)
        assert [row[-1] for row in upgraded["api_keys"]] == [None]
        upgraded["api_keys"] = [row[:-1] for row in upgraded["api_keys"]]
        # Revision 0016 adds two empty personal filter rule tables.
        assert upgraded.pop("user_filter_rule_overrides") == []
        assert upgraded.pop("user_filter_personal_rules") == []
        # Revision 0017 adds three empty account profile tables.
        for table in ("user_profiles", "user_avatars", "account_settings"):
            assert upgraded.pop(table) == []
        drop_0018_additions(upgraded)
        drop_0031_additions(upgraded, original)
        assert without_stable_media_identity(upgraded) == original
    if "api_keys" in original:
        assert _keys(path) == [(DIGEST, KEY if readable else None, None)]
        assert _seal(path) == (1 if readable else 0)
        ((digest, plaintext, envelope),) = _keys(path)
        assert (digest, plaintext) == (DIGEST, None)
        assert (envelope is not None) is readable
        files = (path, path.with_name(path.name + "-wal"))
        assert KEY.encode() not in b"".join(f.read_bytes() for f in files if f.exists())
        assert _seal(path) == 0
        engine = create_engine(f"sqlite:///{path}")
        with Session(engine) as db:
            holder = authenticate_api_key(db, KEY)
            assert holder is not None
            stored = stored_api_key(db, holder.user_id, load_api_key_wrap_key(path.parent))
            assert stored.value == (KEY if readable else None)
        engine.dispose()
    assert adoption.upgrade_existing_database(path, path.parent) is None
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert generation.is_dir()
    assert adoption._inspect_database(recovered) == revision
    assert _snapshot(recovered)[0] == original


def test_sealing_drops_plaintext_that_does_not_match_its_digest(tmp_path):
    path, _ = _with_key(tmp_path, MEDIA_ACCESS)
    with sqlite3.connect(path) as database:
        database.execute("UPDATE api_keys SET token_value = ?", ("f" * 64,))
    adoption.upgrade_existing_database(path, path.parent)
    assert _seal(path) == 1
    assert _keys(path) == [(DIGEST, None, None)]


def test_one_row_cannot_hold_plaintext_and_a_wrapped_copy(tmp_path):
    path, _ = _with_key(tmp_path, MEDIA_ACCESS)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database, pytest.raises(sqlite3.IntegrityError):
        database.execute("UPDATE api_keys SET secret_envelope = 'v1.wrapped'")


def _fail_on_envelope(monkeypatch):
    import alembic.op

    real = alembic.op.execute

    def fail(statement, *args, **kwargs):
        if "secret_envelope" in str(statement):
            raise RuntimeError("injected 0015 failure")
        return real(statement, *args, **kwargs)

    monkeypatch.setattr(alembic.op, "execute", fail)
    return real


def test_failure_inside_0015_blocks_admission_and_recovers(tmp_path, monkeypatch):
    import alembic.op

    path, _ = _with_key(tmp_path, MEDIA_ACCESS)
    original, _ = _snapshot(path)
    real = _fail_on_envelope(monkeypatch)
    with pytest.raises(RuntimeError, match="injected 0015 failure"):
        adoption.upgrade_existing_database(path, path.parent)
    monkeypatch.setattr(alembic.op, "execute", real)
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert (journal["status"], journal["from_revision"]) == ("failed", MEDIA_ACCESS)
    _assert_recoverable(path, original, MEDIA_ACCESS)


def test_hard_exit_inside_0015_blocks_admission_and_recovers(tmp_path):
    path, _ = _with_key(tmp_path, MEDIA_ACCESS)
    original, _ = _snapshot(path)
    script = (
        "import os, sys, alembic.op; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        "real = alembic.op.execute; "
        "alembic.op.execute = lambda statement, *a, **k: os._exit(95) "
        "if 'secret_envelope' in str(statement) else real(statement, *a, **k); "
        "adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(path.parent)], check=False
    )
    assert result.returncode == 95
    assert json.loads((path.parent / adoption.JOURNAL_NAME).read_text())["status"] == "intent"
    _assert_recoverable(path, original, MEDIA_ACCESS)


def test_a_partly_applied_0015_shape_is_refused_without_a_snapshot(tmp_path):
    path, _ = _with_key(tmp_path, MEDIA_ACCESS)
    with sqlite3.connect(path) as database:
        database.execute("ALTER TABLE api_keys ADD COLUMN secret_envelope TEXT")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption.upgrade_existing_database(path, path.parent)
    assert path.read_bytes() == before
    assert not (path.parent / adoption.JOURNAL_NAME).exists()
    assert not (path.parent / "migration-snapshots").exists()


def test_a_head_without_the_one_secret_check_is_refused(tmp_path):
    path, _ = _with_key(tmp_path, MEDIA_ACCESS)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("ALTER TABLE api_keys DROP COLUMN secret_envelope")
        database.execute("ALTER TABLE api_keys ADD COLUMN secret_envelope TEXT")
    with pytest.raises(ValueError, match="Unsupported versioned database schema"):
        adoption._inspect_database(path)


def test_an_unknown_revision_is_refused_without_a_snapshot(tmp_path):
    path, _ = _with_key(tmp_path, MEDIA_ACCESS)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute("UPDATE alembic_version SET version_num = '0016_future_revision'")
    snapshots = sorted((path.parent / "migration-snapshots").iterdir())
    with pytest.raises(ValueError, match="Unsupported database revision"):
        adoption.upgrade_existing_database(path, path.parent)
    assert sorted((path.parent / "migration-snapshots").iterdir()) == snapshots


def _issue(directory):
    engine = create_engine(f"sqlite:///{directory / 'scanner.db'}")
    try:
        with Session(engine) as db:
            return rotate_api_key(db, 1, load_api_key_wrap_key(directory))
    finally:
        engine.dispose()


def _revealed(directory):
    engine = create_engine(f"sqlite:///{directory / 'scanner.db'}")
    try:
        with Session(engine) as db:
            return stored_api_key(db, 1, load_api_key_wrap_key(directory)).value
    finally:
        engine.dispose()


def test_backup_restore_moves_the_wrap_key_with_the_accounts(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    key = _issue(source)
    wrap = (source / "secrets" / WRAP_KEY_FILENAME).read_bytes()
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    assert (target / "secrets" / WRAP_KEY_FILENAME).read_bytes() == wrap
    assert _revealed(target) == key


def test_restoring_an_older_backup_seals_its_plaintext_with_the_live_wrap_key(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    with sqlite3.connect(source / "scanner.db") as database:
        database.execute(
            "UPDATE api_keys SET token_digest = ?, token_value = ?, secret_envelope = NULL",
            (DIGEST, KEY),
        )
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    assert seal_database_api_keys(target / "scanner.db", target) == 1
    assert _keys(target / "scanner.db")[0][:2] == (DIGEST, None)
    assert _revealed(target) == KEY
