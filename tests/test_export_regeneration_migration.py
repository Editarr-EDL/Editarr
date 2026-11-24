"""0022 moves regeneration work out of snapshot JSON without losing rows or recovery."""

import json
import sqlite3
import subprocess
import sys

import pytest

from scanner.persistence.migrations import adoption
from tests.activity_migration_support import _clone, _upgrade
from tests.community_master_migration_support import retire_publications
from tests.explicit_filter_modes_support import drop_0031_additions
from tests.stable_media_identity_migration_support import without_stable_media_identity
from tests.test_account_migration import _snapshot
from tests.test_review_policy_migration import _built

PREVIOUS = "0021_export_snapshots"
JOBS = {
    "queued": {"state": "queued", "policy_owner_id": None, "policy_revision": 1, "attempts": 0},
    "running": {
        "state": "running",
        "policy_owner_id": None,
        "policy_revision": 2,
        "attempts": 2,
        "token": "claim-token",
        "last_attempt": 1000.5,
        "next_attempt_at": 1010.5,
    },
    "succeeded": {
        "state": "succeeded",
        "policy_owner_id": None,
        "policy_revision": 3,
        "attempts": 1,
        "token": "spent-token",
        "last_attempt": 5,
        "next_attempt_at": 10,
        "error": None,
    },
    "exhausted": {
        "state": "failed",
        "policy_owner_id": None,
        "policy_revision": 4,
        "attempts": 5,
        "last_attempt": 20,
        "next_attempt_at": 320,
        "error": "write_failed",
    },
    "withdrawn": {
        "state": "failed",
        "policy_owner_id": None,
        "policy_revision": 5,
        "attempts": 1,
        "error": "write_failed",
    },
}
EXPECTED = {
    "queued": ("queued", 0, 0, None, 1, None, None),
    "running": ("running", 1010.5, 2, 1000.5, 2, "claim-token", None),
    "succeeded": ("succeeded", None, 1, 5.0, 3, None, None),
    "exhausted": ("failed", None, 5, 20.0, 4, None, "retries_exhausted"),
    "withdrawn": ("withdrawn", None, 1, None, 5, None, "regeneration_failed"),
}


def _publications(tmp_path, jobs):
    """Return a 0021 database with one personal publication per named snapshot extra."""
    path = _built(tmp_path)
    _upgrade(path, PREVIOUS)
    with sqlite3.connect(path) as database:
        source = database.execute("SELECT id FROM media_item_exports ORDER BY id").fetchone()[0]
        user = database.execute("SELECT id FROM users ORDER BY id").fetchone()[0]
        ids = {}
        for number, (name, extra) in enumerate(jobs.items(), start=1):
            snapshot = {"version": "editarr-export-snapshot-1", "publication_id": name, **extra}
            if "regeneration" in snapshot:
                snapshot["regeneration"] = dict(snapshot["regeneration"], policy_owner_id=user)
            _clone(
                database,
                "media_item_exports",
                source,
                id=None,
                media_source_id=f"mu34-{name}",
                scope="personal",
                target_user_id=user,
                target_export_id=f"local-{number:032x}",
                actor_id=user,
                policy_owner_id=user,
                original_author_id=user,
                policy_revision=0,
                content_bytes=None if name == "withdrawn" else b"retained bytes\n",
                destination_path=f"/synthetic/{name}.edl",
                snapshot_json=json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
            )
            ids[name] = database.execute("SELECT max(id) FROM media_item_exports").fetchone()[0]
    return path, ids, user


def test_every_json_job_moves_exactly_and_snapshot_keeps_other_evidence(tmp_path):
    extras = {name: {"regeneration": job} for name, job in JOBS.items()}
    extras["recovery"] = {"recovery_required": True}
    extras["untouched"] = {}
    path, ids, user = _publications(tmp_path, extras)
    original, _ = _snapshot(path)
    with sqlite3.connect(path) as database:
        documents = dict(
            database.execute("SELECT id, snapshot_json FROM media_item_exports").fetchall()
        )
    generation = adoption.upgrade_existing_database(path, path.parent)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
        rows = {
            row[0]: row[1:]
            for row in database.execute(
                "SELECT export_id, state, due_at, attempts, last_attempt_at, policy_revision, "
                "claim_token, failure_reason, policy_owner_id "
                "FROM media_item_export_regenerations"
            )
        }
        stored = dict(
            database.execute("SELECT id, snapshot_json FROM media_item_exports").fetchall()
        )
    assert rows == {ids[name]: (*EXPECTED[name], user) for name in JOBS}
    for export_id, document in documents.items():
        if document is None:
            assert stored[export_id] is None
            continue
        expected = json.loads(document)
        expected.pop("regeneration", None)
        expected.pop("recovery_required", None)
        assert json.loads(stored[export_id]) == expected
    assert stored[ids["untouched"]] == documents[ids["untouched"]]
    upgraded, _ = _snapshot(path)
    drop_0031_additions(upgraded, original)
    assert upgraded.pop("media_item_export_regenerations")
    assert upgraded.pop("account_link_transitions") == []
    assert upgraded.pop("account_link_export_moves") == []
    assert upgraded.pop("account_dispositions") == []
    assert upgraded.pop("account_transfer_moves") == []
    assert upgraded.pop("jellyfin_user_blocks") == []
    unchanged = without_stable_media_identity(upgraded)
    unchanged.pop("media_item_exports")
    expected = retire_publications(original, upgraded)
    assert unchanged == {name: rows for name, rows in expected.items() if name in unchanged}
    assert len(upgraded["media_item_exports"]) == len(original["media_item_exports"])
    assert adoption.upgrade_existing_database(path, path.parent) is None
    restored = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "restored.db")
    assert generation.is_dir()
    assert adoption._inspect_database(restored) == PREVIOUS
    assert _snapshot(restored)[0] == original


@pytest.mark.parametrize(
    "job",
    [
        {"state": "withdrawing", "policy_owner_id": None, "policy_revision": 1, "attempts": 0},
        {"state": "running", "policy_owner_id": None, "policy_revision": 1, "attempts": 1},
        {"state": "queued", "policy_owner_id": None, "policy_revision": -1, "attempts": 0},
    ],
)
def test_unrecognized_json_job_refuses_upgrade_with_verified_recovery(tmp_path, job):
    from scanner.persistence.migrations import preflight

    path, _ids, _user = _publications(tmp_path, {"odd": {"regeneration": job}})
    original, _ = _snapshot(path)
    with pytest.raises(ValueError, match="Unsupported regeneration"):
        adoption.upgrade_existing_database(path, path.parent)
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert (journal["from_revision"], journal["status"]) == (PREVIOUS, "failed")
    with pytest.raises(RuntimeError, match="unresolved"):
        preflight.inspect_schema(path, path.parent)
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert adoption._inspect_database(recovered) == PREVIOUS
    assert _snapshot(recovered)[0] == original


@pytest.mark.parametrize("interrupted", [False, True])
def test_partial_0022_upgrade_requires_verified_recovery(tmp_path, interrupted):
    from tests.test_activity_migration import _assert_recoverable

    path, _ids, _user = _publications(tmp_path, {"queued": {"regeneration": JOBS["queued"]}})
    original, _ = _snapshot(path)
    script = """
import os, sys, alembic.op
from pathlib import Path
from scanner.persistence.migrations import adoption
real = alembic.op.create_index
def fail(name, *args, **kwargs):
    result = real(name, *args, **kwargs)
    if name == "ix_export_regenerations_due":
        if sys.argv[3] == "True":
            os._exit(97)
        raise RuntimeError("injected 0022 failure")
    return result
alembic.op.create_index = fail
adoption.upgrade_existing_database(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(path), str(path.parent), str(interrupted)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == (97 if interrupted else 1), result.stderr.decode()
    journal = json.loads((path.parent / adoption.JOURNAL_NAME).read_text())
    assert journal["from_revision"] == PREVIOUS
    assert journal["status"] == ("intent" if interrupted else "failed")
    _assert_recoverable(path, original, PREVIOUS)


def test_older_supported_revision_reaches_head_with_an_empty_queue(tmp_path):
    path = _built(tmp_path)
    _upgrade(path, "0020_review_scan_repairs")
    original, _ = _snapshot(path)
    adoption.upgrade_existing_database(path, path.parent)
    upgraded, _ = _snapshot(path)
    drop_0031_additions(upgraded, original)
    assert upgraded.pop("media_item_export_regenerations") == []
    assert upgraded.pop("account_link_transitions") == []
    assert upgraded.pop("account_link_export_moves") == []
    assert upgraded.pop("account_dispositions") == []
    assert upgraded.pop("account_transfer_moves") == []
    assert upgraded.pop("jellyfin_user_blocks") == []
    assert all(row[-5:] == (None, None, None, 0, None) for row in upgraded["media_item_exports"])
    upgraded["media_item_exports"] = [row[:-5] for row in upgraded["media_item_exports"]]
    assert without_stable_media_identity(upgraded) == retire_publications(original, upgraded)


@pytest.mark.parametrize(
    ("state", "due_at", "reason", "accepted"),
    [
        ("withdrawn", None, "deleted", True),
        ("withdrawing", 1.0, "deleted", False),
        ("failed", None, "deleted", False),
        ("withdrawn", None, "removed", False),
    ],
)
def test_upgraded_queue_records_a_deletion_only_as_a_withdrawal(
    tmp_path, state, due_at, reason, accepted
):
    path, ids, _user = _publications(tmp_path, {"deleted": {}})
    adoption.upgrade_existing_database(path, path.parent)
    insert = (
        "INSERT INTO media_item_export_regenerations (export_id, state, due_at, attempts, "
        "policy_revision, failure_reason) VALUES (?, ?, ?, 0, 0, ?)"
    )
    with sqlite3.connect(path) as database:
        if accepted:
            database.execute(insert, (ids["deleted"], state, due_at, reason))
        else:
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(insert, (ids["deleted"], state, due_at, reason))
