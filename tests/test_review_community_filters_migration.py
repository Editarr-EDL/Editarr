"""Upgrade to 0040 turns stored community snapshot filters into review filters, losing nothing."""

import json
import sqlite3

import pytest
from sqlalchemy import create_engine

from scanner.backup.snapshots import validate_safety_snapshot
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import upgrade_connection
from tests.community_identity_migration_support import no_network as no_network

pytestmark = pytest.mark.real_migrations

SOURCE = "0039_review_community_sources"
_TIME = "2026-01-01 00:00:00"
_ENTRY = {
    "category": "violence",
    "subcategory": "punching",
    "severity": "high",
    "edit_action": "skip",
    "confidence": 0.4,
}
ENTRIES = [
    {**_ENTRY, "filter_id": "f_skip", "start_ms": 10_000, "end_ms": 20_000},
    {**_ENTRY, "filter_id": "f_mute", "edit_action": "mute", "start_ms": 30_000, "end_ms": 31_000},
    {
        **_ENTRY,
        "filter_id": "f_wrong",
        "subcategory": "swearing",
        "start_ms": 40_000,
        "end_ms": 41_000,
    },
    {**_ENTRY, "filter_id": "f_early", "start_ms": -500, "end_ms": 1_000},
    {**_ENTRY, "filter_id": "f_before", "start_ms": -900, "end_ms": -100},
]
_FILTERS = (
    "SELECT community_filter_id, source, category, subcategory, severity, mcf_channel, "
    "start_seconds, end_seconds, confidence, edit_action, selected, position, source_scan_id "
    "FROM review_filters ORDER BY position"
)


def _seed(path):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as db:
            upgrade_connection(db, SOURCE)
            db.exec_driver_sql(
                "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
                "created_at, updated_at) VALUES ('item', ?, ?, 'Movie', 'M', ?, ?)",
                ("s" * 32, "j" * 32, _TIME, _TIME),
            )
            db.exec_driver_sql(
                "INSERT INTO saved_reviews (id, media_item_id, name, created_at, updated_at) "
                "VALUES ('review', 'item', 'Review', ?, ?)",
                (_TIME, _TIME),
            )
            db.exec_driver_sql(
                "INSERT INTO review_community_sources (review_id, id, link_id, position, "
                "community_version_id, label, master_revision, filters_json, filters_digest, "
                "captured_at) VALUES ('review', 'src', NULL, 0, 'v_1', 'Theatrical', 1, ?, ?, ?)",
                (json.dumps(ENTRIES), "0" * 64, _TIME),
            )
    finally:
        engine.dispose()


def _read(path, query):
    with sqlite3.connect(path) as database:
        return database.execute(query).fetchall()


def test_upgrade_adds_each_snapshot_filter_to_its_review_recoverably(tmp_path):
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700)
    _seed(path)
    before = _read(path, "SELECT * FROM review_community_sources")
    assert adoption._inspect_database(path) == SOURCE

    generation = adoption.upgrade_existing_database(path, path.parent)

    validate_safety_snapshot(generation)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert _read(path, _FILTERS) == [
        (
            "f_skip",
            "visual",
            "violence",
            "punching",
            "high",
            "both",
            10.0,
            20.0,
            0.4,
            "skip",
            1,
            0,
            None,
        ),
        (
            "f_mute",
            "audio",
            "violence",
            "punching",
            "high",
            "audio",
            30.0,
            31.0,
            0.4,
            "mute",
            1,
            1,
            None,
        ),
        ("f_wrong", "visual", None, None, "high", "both", 40.0, 41.0, 0.4, "skip", 0, 2, None),
        (
            "f_early",
            "visual",
            "violence",
            "punching",
            "high",
            "both",
            0.0,
            1.0,
            0.4,
            "skip",
            1,
            3,
            None,
        ),
    ]
    (stored,) = _read(path, "SELECT filters_json, filters_digest FROM review_community_sources")
    entries = {entry["filter_id"]: entry for entry in json.loads(stored[0])}
    assert {fid: entry["default_selected"] for fid, entry in entries.items()} == {
        "f_skip": True,
        "f_mute": True,
        "f_wrong": False,
        "f_early": True,
        "f_before": True,
    }
    assert entries["f_early"]["initial"] == {"start_ms": 0, "end_ms": 1_000}
    assert entries["f_before"]["outside"] is True
    assert stored[1] == "0" * 64
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert adoption._inspect_database(recovered) == SOURCE
    assert _read(recovered, "SELECT * FROM review_community_sources") == before


def test_downgrade_refuses_to_discard_community_review_filters(tmp_path):
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = tmp_path / "scanner.db"
    _seed(path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as db:
            upgrade_connection(db, adoption.HEAD_REVISION)
        migration = import_module(
            "scanner.persistence.migrations.versions.0040_review_community_filters"
        )
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            with pytest.raises(RuntimeError, match="discarding review filters"):
                migration.downgrade()
    finally:
        engine.dispose()
    assert len(_read(path, "SELECT id FROM review_filters")) == 4
