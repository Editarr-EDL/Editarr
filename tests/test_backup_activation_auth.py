"""Restore ignores the inert legacy auth mode and refuses an archive without its owner."""

import sqlite3

import pytest

from scanner.backup.activation import GENERATION, JOURNAL
from scanner.backup.archive import export_backup
from scanner.backup.restore_target import RestoreRefused
from tests.test_backup_activation import _fixture, _restore

_LEGACY_AUTH_MODE = (
    "INSERT INTO auth_settings (id, method, authentication_required, updated_at) "
    "VALUES (1, 'forms', ?, '2026-09-27')"
)


def _reexport(tmp_path, statement, *parameters):
    source, archive = tmp_path / "source", tmp_path / "portable.backup"
    with sqlite3.connect(source / "scanner.db") as database:
        database.execute(statement, parameters)
    archive.unlink()
    export_backup(source / "scanner.db", source, archive)


@pytest.mark.parametrize("legacy_mode", [None, "disabled_for_local_addresses"])
def test_an_inert_legacy_auth_mode_does_not_block_restore(tmp_path, legacy_mode):
    archive, target = _fixture(tmp_path)
    if legacy_mode is not None:
        _reexport(tmp_path, _LEGACY_AUTH_MODE, legacy_mode)
    _restore(archive, target)
    with sqlite3.connect(target / "scanner.db") as database:
        assert database.execute("SELECT username FROM users").fetchall() == [("source",)]


def test_an_archive_without_its_owner_is_refused_without_target_changes(tmp_path):
    archive, target = _fixture(tmp_path)
    _reexport(tmp_path, "UPDATE users SET role = 'admin'")
    before = (target / "scanner.db").read_bytes()
    with pytest.raises(RestoreRefused) as refused:
        _restore(archive, target)
    assert refused.value.reason == "restore_owner_count"
    assert not (target / JOURNAL).exists()
    assert not (target / GENERATION).exists()
    assert (target / "scanner.db").read_bytes() == before
