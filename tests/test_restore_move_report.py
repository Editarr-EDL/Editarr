"""The restore report lists move records that disagree with the disk, and nothing else."""

import sqlite3

from scanner.backup.move_report import (
    RECORDED_PATH_MISSING,
    UNRECORDED_COPY,
    move_disagreements,
)
from tests.test_backup_account_family import CONFIRMATION, _install, _publication


def _move(database, export_id, old_path, new_path, outcome=None):
    database.execute(
        "INSERT INTO account_transfer_moves (user_id, confirmation, export_id, old_path, "
        "new_path, outcome) VALUES (2, ?, ?, ?, ?, ?)",
        (CONFIRMATION, export_id, str(old_path), str(new_path), outcome),
    )


def _findings(path):
    return {(item["export_id"], item["finding"], item["path"]) for item in move_disagreements(path)}


def test_settled_moves_that_match_the_disk_are_not_reported(tmp_path):
    database = _install(tmp_path / "data", members=True)
    old, new, kept = tmp_path / "old.edl", tmp_path / "new.edl", tmp_path / "kept.edl"
    new.write_bytes(b"published")
    kept.write_bytes(b"the user's own file")
    with sqlite3.connect(database) as connection:
        _publication(connection, 1, 2, str(new))
        _move(connection, 1, old, new, "removed")
        _publication(connection, 2, 2, str(tmp_path / "second.edl"))
        (tmp_path / "second.edl").write_bytes(b"published")
        _move(connection, 2, kept, tmp_path / "second.edl", "kept")
    assert _findings(database) == set()


def test_moves_that_disagree_with_the_disk_are_reported(tmp_path):
    database = _install(tmp_path / "data", members=True)
    old, new = tmp_path / "old.edl", tmp_path / "new.edl"
    stray_old, stray_new = tmp_path / "stray-old.edl", tmp_path / "stray-new.edl"
    new.write_bytes(b"placed but never switched")
    stray_old.write_bytes(b"left behind")
    with sqlite3.connect(database) as connection:
        _publication(connection, 1, 2, str(old))
        _move(connection, 1, old, new)
        _move(connection, None, stray_old, stray_new)
    assert _findings(database) == {
        (1, RECORDED_PATH_MISSING, str(old)),
        (1, UNRECORDED_COPY, str(new)),
        (None, UNRECORDED_COPY, str(stray_old)),
    }
    assert old.exists() is False and new.read_bytes() == b"placed but never switched"
