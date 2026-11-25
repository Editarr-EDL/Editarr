"""Upgrade to 0040 turns stored community snapshot filters into review filters, losing nothing.

Snapshots of a link measure their stored filters against the link's master values, so
the owner's earlier Community-tab decisions become review edits.
"""

import json
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import scanner.api  # noqa: F401 - load the application before repository modules
from scanner.backup.snapshots import validate_safety_snapshot
from scanner.db import SavedReviewRow
from scanner.modeling.community_review_filters import snapshot_digest
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import upgrade_connection
from scanner.persistence.repository.review_community_annotations import annotations
from scanner.persistence.repository.review_community_sources import review_sources
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
# A snapshot without a link: its stored filters are their own defaults.
DETACHED = [
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
# Master revision 3 of the link: raw master timeline, aligned by +1000 ms locally.
MASTER = [
    {"filterId": "f_ok", "startMs": 100_000, "endMs": 101_000, "confidence": 0.8},
    {"filterId": "f_adj", "startMs": 200_000, "endMs": 201_000, "confidence": 0.8},
    {"filterId": "f_rej", "startMs": 300_000, "endMs": 301_000, "confidence": 0.8},
    {"filterId": "f_low", "startMs": 400_000, "endMs": 401_000, "confidence": 0.1},
    {"filterId": "f_touch", "startMs": 500_000, "endMs": 501_000, "confidence": 0.1},
]
STATES = {"f_ok": "pending", "f_adj": "adjusted", "f_rej": "rejected", "f_low": "pending"}
STATES["f_touch"] = "verified"


def _master(item):
    return {
        "mergedIds": [],
        "category": "violence",
        "subcategory": "punching",
        "severity": "high",
        "action": "skip",
        "lowConfidence": False,
        "contributorCount": 3,
        **item,
    }


def _local(item, **changes):
    """A 0039 snapshot entry: the active filter's effective values on the local timeline."""
    return {
        **_ENTRY,
        "filter_id": item["filterId"],
        "start_ms": item["startMs"] + 1000,
        "end_ms": item["endMs"] + 1000,
        "confidence": item["confidence"],
        **changes,
    }


# The 0039 snapshot left out the rejected and below-threshold filters.
LINKED = [
    _local(MASTER[0]),
    _local(MASTER[1], start_ms=201_500, severity="low"),
    _local(MASTER[4]),
]
_FILTERS = (
    "SELECT community_filter_id, source, category, subcategory, severity, mcf_channel, "
    "start_seconds, end_seconds, confidence, edit_action, selected, position, source_scan_id, "
    "community_edited FROM review_filters WHERE review_id = ? ORDER BY position"
)
_SCAN_FILTERS = "SELECT * FROM review_filters WHERE source_scan_id IS NOT NULL ORDER BY id"


def _insert_source(db, review_id, source_id, link_id, entries, position=0):
    db.exec_driver_sql(
        "INSERT INTO review_community_sources (review_id, id, link_id, position, "
        "community_version_id, label, master_revision, alignment_json, filters_json, "
        "filters_digest, captured_at) VALUES (?, ?, ?, ?, 'v_1', 'Theatrical', 3, ?, ?, ?, ?)",
        (
            review_id,
            source_id,
            link_id,
            position,
            json.dumps({"offsetMs": 1000, "scale": 1.0, "anchors": 1}) if link_id else None,
            json.dumps(entries),
            "0" * 64,
            _TIME,
        ),
    )


def _seed(path):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as db:
            upgrade_connection(db, SOURCE)
            db.exec_driver_sql(
                "INSERT INTO users (id, role, status, local_export_id, permission_revision, "
                "created_at, updated_at) VALUES (1, 'owner', 'active', ?, 1, ?, ?)",
                ("local-" + "e" * 32, _TIME, _TIME),
            )
            db.exec_driver_sql(
                "INSERT INTO community_master_preferences (owner_id, confidence_threshold, "
                "show_low_confidence, hide_downvoted) VALUES (1, 0.3, 0, 0)"
            )
            db.exec_driver_sql(
                "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
                "created_at, updated_at) VALUES ('item', ?, ?, 'Movie', 'M', ?, ?)",
                ("s" * 32, "j" * 32, _TIME, _TIME),
            )
            db.exec_driver_sql("INSERT INTO media_titles (id, entity_type) VALUES ('t', 'movie')")
            db.exec_driver_sql("INSERT INTO media_versions (id, title_id) VALUES ('mv', 't')")
            db.exec_driver_sql(
                "INSERT INTO scans (id, media_item_id, scan_number, source_path, "
                "resolved_local_path, status, scan_audio, scan_video, speed_up, qwen_model, "
                "qwen_summaries_per_segment, qwen_segment_join_gap_seconds, selection_revision, "
                "visual_checkpoint_complete, audio_extraction_checkpoint_complete, "
                "audio_checkpoint_complete, checkpoint_stage, checkpoint_state, "
                "created_at, updated_at) VALUES ('scan', 'item', 1, '/m', '/m', 'complete', "
                "0, 1, 0, 'q', 4, 3.0, 0, 0, 0, 0, 'queued', '{}', ?, ?)",
                (_TIME, _TIME),
            )
            db.exec_driver_sql(
                "INSERT INTO scan_findings (id, scan_id, category, subcategory, start_seconds, "
                "end_seconds, source, selected, edit_action, created_at) VALUES "
                "(7, 'scan', 'violence', 'punching', 5.0, 6.0, 'visual', 1, 'skip', ?)",
                (_TIME,),
            )
            for review_id in ("review-a", "review-b"):
                db.exec_driver_sql(
                    "INSERT INTO saved_reviews (id, media_item_id, name, owner_id, "
                    "original_author_id, created_at, updated_at) "
                    "VALUES (?, 'item', 'Review', 1, 1, ?, ?)",
                    (review_id, _TIME, _TIME),
                )
            db.exec_driver_sql(
                "INSERT INTO review_filters (review_id, id, source_scan_id, source_finding_id, "
                "position, source, category, subcategory, severity, start_seconds, end_seconds, "
                "confidence, edit_action, selected) VALUES ('review-a', 'scan-filter', 'scan', "
                "7, 0, 'visual', 'violence', 'punching', 'medium', 5.0, 6.0, 0.9, 'skip', 0)"
            )
            db.exec_driver_sql(
                "INSERT INTO community_master_links (id, owner_id, media_version_id, "
                "service_url, community_version_id, version_status, applied_revision, "
                "latest_revision, alignment_state, alignment_json, alignment_revision, "
                "link_state, created_at, updated_at) VALUES ('link', 1, 'mv', 'https://c', "
                "'v_1', 'confirmed', 3, 3, 'approved', ?, 3, 'active', ?, ?)",
                (json.dumps({"offsetMs": 1000, "scale": 1.0, "anchors": 1}), _TIME, _TIME),
            )
            db.exec_driver_sql(
                "INSERT INTO community_master_snapshots (id, link_id, revision, "
                "snapshot_digest, snapshot_bytes, signature_json, service_key_id, receipt_json, "
                "receipt_id, downloaded_at) VALUES ('snap', 'link', 3, ?, ?, '{}', 'k', '{}', "
                "'r', ?)",
                ("d" * 64, json.dumps({"filters": [_master(item) for item in MASTER]}), _TIME),
            )
            for item in MASTER:
                master = _master(item)
                baseline = {
                    key: master[key]
                    for key in ("startMs", "endMs", "subcategory", "severity", "action")
                }
                db.exec_driver_sql(
                    "INSERT INTO community_master_filters (link_id, filter_id, category, "
                    "baseline_json, baseline_revision, info_json, state, locally_verified, "
                    "local_touched, present, updated_at) "
                    "VALUES ('link', ?, 'violence', ?, 3, ?, ?, 0, ?, 1, ?)",
                    (
                        item["filterId"],
                        json.dumps({**baseline, "lowConfidence": False, "present": True}),
                        json.dumps({"confidence": item["confidence"], "contributorCount": 3}),
                        STATES[item["filterId"]],
                        STATES[item["filterId"]] != "pending",
                        _TIME,
                    ),
                )
            _insert_source(db, "review-a", "src-linked", "link", LINKED)
            _insert_source(db, "review-a", "src-detached", None, DETACHED, position=1)
            _insert_source(db, "review-b", "src-b", "link", LINKED[:1])
    finally:
        engine.dispose()


def _read(path, query, *args):
    with sqlite3.connect(path) as database:
        return database.execute(query, args).fetchall()


@pytest.fixture
def upgraded(tmp_path):
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700)
    _seed(path)
    before = {
        "sources": _read(path, "SELECT * FROM review_community_sources ORDER BY id"),
        "scan_filters": _read(path, _SCAN_FILTERS),
    }
    assert adoption._inspect_database(path) == SOURCE
    generation = adoption.upgrade_existing_database(path, path.parent)
    validate_safety_snapshot(generation)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    return path, before


def test_snapshot_filters_become_review_filters_after_existing_ones(upgraded):
    path, before = upgraded
    scan_filters = _read(path, _SCAN_FILTERS)
    # Scan filters keep every value, including their positions; 0040 adds null columns.
    assert [row[:-3] for row in scan_filters] == before["scan_filters"]
    assert [row[-3:] for row in scan_filters] == [(None, None, None)]
    rows = _read(path, _FILTERS, "review-a")
    linked = [row for row in rows if row[0] in {item["filterId"] for item in MASTER}]
    assert [row[11] for row in rows] == list(range(len(rows)))
    assert [(row[0], row[6], row[7], row[4], row[10], row[13]) for row in linked] == [
        ("f_ok", 101.0, 102.0, "high", 1, 0),
        ("f_adj", 201.5, 202.0, "low", 1, 1),
        ("f_rej", 301.0, 302.0, "high", 0, 1),
        ("f_low", 401.0, 402.0, "high", 0, 0),
        ("f_touch", 501.0, 502.0, "high", 1, 1),
    ]
    assert rows[0][0] is None and rows[0][12] == "scan"
    detached = [row for row in rows[1:] if row[0] not in {item["filterId"] for item in MASTER}]
    assert [row[:11] for row in detached] == [
        ("f_early", "visual", "violence", "punching", "high", "both", 0.0, 1.0, 0.4, "skip", 1),
        ("f_skip", "visual", "violence", "punching", "high", "both", 10.0, 20.0, 0.4, "skip", 1),
        ("f_mute", "audio", "violence", "punching", "high", "audio", 30.0, 31.0, 0.4, "mute", 1),
        ("f_wrong", "visual", None, None, "high", "both", 40.0, 41.0, 0.4, "skip", 0),
    ]
    assert all(row[12] is None and row[13] == 0 for row in detached)
    assert [row[0] for row in _read(path, _FILTERS, "review-b")] == [
        "f_ok",
        "f_adj",
        "f_rej",
        "f_low",
        "f_touch",
    ]


def test_snapshots_hold_master_defaults_and_read_current_against_their_link(upgraded):
    path, before = upgraded
    stored = {
        row[0]: (json.loads(row[1]), row[2])
        for row in _read(
            path, "SELECT id, filters_json, filters_digest FROM review_community_sources"
        )
    }
    for entries, digest in stored.values():
        assert digest == snapshot_digest(entries)
    linked = {entry["filter_id"]: entry for entry in stored["src-linked"][0]}
    assert {fid: entry["default_selected"] for fid, entry in linked.items()} == {
        "f_ok": True,
        "f_adj": True,
        "f_rej": True,
        "f_low": False,
        "f_touch": False,
    }
    assert (linked["f_adj"]["start_ms"], linked["f_adj"]["severity"]) == (201_000, "high")
    detached = {entry["filter_id"]: entry for entry in stored["src-detached"][0]}
    assert detached["f_early"]["initial"] == {"start_ms": 0, "end_ms": 1_000}
    assert detached["f_before"]["outside"] is True
    assert not detached["f_wrong"]["default_selected"]
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as db:
            review = db.get(SavedReviewRow, "review-a")
            states = {source.source_id: source.state for source in review_sources(db, review)}
            assert states == {"src-linked": "current", "src-detached": "link_unavailable"}
            assert review_sources(db, db.get(SavedReviewRow, "review-b"))[0].state == "current"
            assert all(not value.removed for value in annotations(db, review).values())
    finally:
        engine.dispose()
    recovered = adoption.export_upgrade_recovery(path, path.parent, path.parent / "rec.db")
    assert adoption._inspect_database(recovered) == SOURCE
    assert (
        _read(recovered, "SELECT * FROM review_community_sources ORDER BY id")
        == (before["sources"])
    )


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
    assert len(_read(path, "SELECT id FROM review_filters")) == 15
