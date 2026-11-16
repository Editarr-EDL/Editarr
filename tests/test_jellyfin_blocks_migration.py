"""0029 adds an empty `jellyfin_user_blocks` table from every supported revision."""

import sqlite3

import pytest

from scanner.persistence.migrations import adoption
from tests.activity_migration_support import _upgrade
from tests.test_upgrade_matrix import SOURCES

_SERVER = "a" * 32
_USER_ID = "b" * 32
_BLOCK = (
    "INSERT INTO jellyfin_user_blocks (server_id, provider_user_id, name, blocked_by, blocked_at) "
    "VALUES (?, ?, ?, ?, '2026-11-04 00:00:00')"
)


def _blocks(path):
    with sqlite3.connect(path) as database:
        columns = [row[1:4] for row in database.execute("PRAGMA table_info(jellyfin_user_blocks)")]
        rows = database.execute("SELECT count(*) FROM jellyfin_user_blocks").fetchone()[0]
    return columns, rows


@pytest.mark.parametrize("revision", [revision for revision in SOURCES if revision])
def test_every_supported_revision_gains_an_empty_block_table(tmp_path, revision):
    path = tmp_path / "scanner.db"
    _upgrade(path, revision)

    _upgrade(path, "head")

    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert _blocks(path) == (
        [
            ("server_id", "VARCHAR", 1),
            ("provider_user_id", "VARCHAR", 1),
            ("name", "VARCHAR", 0),
            ("blocked_by", "INTEGER", 0),
            ("blocked_at", "DATETIME", 1),
        ],
        0,
    )


def test_the_block_table_takes_one_row_per_identity_and_refuses_bad_ids(tmp_path):
    path = tmp_path / "scanner.db"
    _upgrade(path, "head")
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA foreign_keys = ON")
        database.execute(_BLOCK, (_SERVER, _USER_ID, "Casey", None))
        for values in (
            (_SERVER, _USER_ID, "Casey again", None),
            ("", _USER_ID, None, None),
            (_SERVER, "B" * 32, None, None),
            (_SERVER, "0" * 32, None, None),
            (_SERVER, "c" * 31, None, None),
            (_SERVER, "c" * 32, "", None),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(_BLOCK, values)
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(_BLOCK, (_SERVER, "d" * 32, None, 999))
        assert database.execute(
            "SELECT server_id, provider_user_id, name FROM jellyfin_user_blocks"
        ).fetchall() == [(_SERVER, _USER_ID, "Casey")]
