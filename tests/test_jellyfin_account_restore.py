"""MU-16 and MU-40: restored Jellyfin accounts are trusted as recorded.

A restore brings back each linked account's provider ID, generation, standing,
and sync state unchanged, clears the read epoch so no read begun elsewhere
applies, and normal synchronization governs afterwards. Synthetic backups go
through production export, preview, and activation into an empty installation,
with the MU-08 `JellyfinBoundary` (`editarr-jellyfin-users-1`).
"""

from __future__ import annotations

import copy
import sqlite3
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scanner import db as database_module
from scanner.backup.archive import export_backup
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security.access.identity import CredentialKind
from scanner.security.jellyfin_accounts import admission, reads
from scanner.security.jellyfin_accounts.sync import sync_accounts
from tests.jellyfin_user_boundary import IDS, NOW
from tests.multi_user_access_support import OWNER
from tests.test_backup_activation import _fixture, _restore
from tests.test_jellyfin_account_sync import boundary as boundary
from tests.test_jellyfin_account_sync import clock as clock


def _seed(directory, boundary, names):
    """Import `names` and take two more reads; returns the new account IDs in ID order."""

    engine = create_engine(f"sqlite:///{directory / 'scanner.db'}")
    try:
        with sessionmaker(bind=engine)() as db:
            with JellyfinUserAdapter(boundary.url, boundary.api_key, clock=lambda: NOW) as adapter:
                listing = adapter.users()
                results = admission.import_users(
                    db,
                    adapter,
                    listing,
                    frozenset(IDS[name] for name in names),
                    actor=OWNER,
                    credential=CredentialKind.SESSION,
                    read=reads.begin_read(db, lambda: NOW),
                )
            reads.begin_read(db, lambda: NOW)
            reads.begin_read(db, lambda: NOW)
    finally:
        engine.dispose()
    return sorted(result.user_id for result in results)


def _factory(target, monkeypatch):
    monkeypatch.setattr(
        database_module,
        "settings",
        SimpleNamespace(data_dir=target, database_url=f"sqlite:///{target / 'scanner.db'}"),
    )
    engine = database_module.get_engine()
    return engine, sessionmaker(bind=engine, autoflush=False)


def _accounts(target):
    """Every linked account's stored Jellyfin state, standing, and profile."""

    with sqlite3.connect(target / "scanner.db") as database:
        return database.execute(
            "SELECT u.id, u.status, u.disable_cause, u.role, i.provider_user_id, "
            "i.observed_generation, i.last_verified_at, s.jellyfin_name, s.last_attempt_at "
            "FROM users u JOIN user_provider_identities i ON i.user_id = u.id "
            "LEFT JOIN jellyfin_account_sync s ON s.user_id = u.id ORDER BY u.id"
        ).fetchall()


def _standing(target, user_id):
    with sqlite3.connect(target / "scanner.db") as database:
        return database.execute(
            "SELECT status, disable_cause FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def _disable_upstream(boundary, name):
    boundary.fixture = copy.deepcopy(boundary.fixture)
    for user in boundary.fixture["states"]["baseline"]["users"]:
        if user["Id"] == IDS[name]:
            user.setdefault("Policy", {})["IsDisabled"] = True


def test_restored_jellyfin_accounts_are_trusted_and_then_synchronized(
    tmp_path, boundary, clock, monkeypatch
):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    (user_id,) = _seed(source, boundary, ["Casey"])
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    assert _accounts(target) == _accounts(source)
    with sqlite3.connect(target / "scanner.db") as database:
        assert database.execute("SELECT jellyfin_read_epoch FROM account_settings").fetchall() == [
            (None,)
        ]
    _disable_upstream(boundary, "Casey")
    engine, factory = _factory(target, monkeypatch)
    try:
        with factory() as db:
            with JellyfinUserAdapter(boundary.url, boundary.api_key, clock=clock) as adapter:
                results = sync_accounts(db, adapter, clock=clock)
    finally:
        engine.dispose()
    # The restored owner is linked to another server than the boundary.
    (result,) = (result for result in results if result.user_id != 1)
    assert result.user_id == user_id
    assert _standing(target, user_id) == ("disabled", "upstream_disabled")
