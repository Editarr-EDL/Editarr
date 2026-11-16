"""Backups carry regeneration work; older backups gain it through the 0022 upgrade."""

import json
import sqlite3

from scanner.backup.archive import _sqlite_snapshot
from scanner.persistence.migrations import adoption
from tests.test_export_regeneration_migration import JOBS, _publications

QUERY = "SELECT export_id, state, policy_revision FROM media_item_export_regenerations"


def _jobs(path):
    with sqlite3.connect(path) as database:
        return sorted(database.execute(QUERY).fetchall())


def test_backup_keeps_regeneration_work(tmp_path):
    extras = {name: {"regeneration": job} for name, job in JOBS.items()}
    extras["deleted"] = {"owned_sha256": "0" * 64}
    path, ids, _user = _publications(tmp_path, extras)
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        database.execute(
            "UPDATE media_item_exports SET content_bytes = NULL WHERE id = ?", (ids["deleted"],)
        )
        database.execute(
            "INSERT INTO media_item_export_regenerations (export_id, state, attempts, "
            "policy_revision, failure_reason) VALUES (?, 'withdrawn', 0, 0, 'deleted')",
            (ids["deleted"],),
        )
    expected = _jobs(path)
    assert {row[0] for row in expected} == set(ids.values())
    full = tmp_path / "full.sqlite3"
    _sqlite_snapshot(path, full)
    assert _jobs(full) == expected
    with sqlite3.connect(full) as database:
        reason, content, document = database.execute(
            "SELECT failure_reason, content_bytes, snapshot_json FROM media_item_exports "
            "JOIN media_item_export_regenerations ON export_id = id WHERE id = ?",
            (ids["deleted"],),
        ).fetchone()
    assert (reason, content) == ("deleted", None)
    assert json.loads(document)["owned_sha256"] == "0" * 64
