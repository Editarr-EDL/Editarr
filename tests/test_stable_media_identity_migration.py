"""Upgrade real older schemas while retaining the original catalog and artifacts."""

import json
import sqlite3

import pytest
from sqlalchemy import create_engine

from scanner.backup.snapshots import validate_safety_snapshot
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import upgrade_connection

_SCAN = (
    "INSERT INTO scans (id, media_item_id, scan_number, source_path, resolved_local_path, "
    "media_source_id, video_hash, media_provider_id, status, scan_audio, scan_video, "
    "speed_up, qwen_model, qwen_summaries_per_segment, qwen_segment_join_gap_seconds, "
    "selection_revision, visual_checkpoint_complete, audio_extraction_checkpoint_complete, "
    "audio_checkpoint_complete, checkpoint_stage, checkpoint_state, created_at, updated_at) "
    "VALUES (?, ?, ?, '/same.mkv', '/mount/same.mkv', 'source', ?, ?, ?, "
    "1, 0, 0, 'model', 4, 3.0, 0, 0, 0, 0, 'queued', '{}', '2026-01-01', '2026-01-01')"
)
_ORIGINAL_TABLES = (
    "media_items",
    "scans",
    "saved_reviews",
    "review_scan_memberships",
    "media_item_exports",
)


def _columns(db):
    return {
        table: ", ".join(row[1] for row in db.execute(f"PRAGMA table_info({table})"))
        for table in _ORIGINAL_TABLES
    }


def _rows(db, columns):
    return {
        table: db.execute(f"SELECT {fields} FROM {table} ORDER BY rowid").fetchall()
        for table, fields in columns.items()
    }


def _build(path, revision):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as db:
            upgrade_connection(db, revision)
            movie_identity = json.dumps(
                {
                    "entity_type": "movie",
                    "identifiers": [
                        {"provider": "tmdb", "entity_type": "movie", "value": "567"},
                        {"provider": "tvdb", "entity_type": "movie", "value": "44"},
                    ],
                }
            )
            episode_identity = json.dumps(
                {
                    "entity_type": "episode",
                    "identifiers": [],
                    "series_identifiers": [
                        {"provider": "tvdb", "entity_type": "series", "value": "44"}
                    ],
                    "episode_numbers": [{"scheme": "aired", "season": 2, "number": 6}],
                }
            )
            db.exec_driver_sql(
                "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
                "confirmed_identity_json, archived_source, created_at, updated_at) "
                "VALUES (?, 'server', ?, ?, 'Movie', ?, ?, '2026-01-01', '2026-01-01')",
                [
                    ("first", "old-jellyfin", "Movie", movie_identity, None),
                    ("replacement", "new-jellyfin", "Movie", None, None),
                    ("tv", "tv-jellyfin", "Episode", episode_identity, None),
                    ("archived", "archived-jellyfin", "Movie", movie_identity, "manual_files"),
                ],
            )
            db.exec_driver_sql(
                _SCAN,
                [
                    ("original", "first", 1, "old-hash", "tmdb:567", "complete"),
                    ("original-audio", "first", 2, "old-hash", "tmdb:567", "complete"),
                    ("changed", "first", 3, "new-hash", "tmdb:567", "complete"),
                    ("other-item", "replacement", 1, "new-hash", "tmdb:0567", "complete"),
                    ("queued-scan", "first", 4, None, "tmdb:567", "queued"),
                    ("archived-scan", "archived", 1, "old-hash", "tmdb:567", "complete"),
                ],
            )
            db.exec_driver_sql(
                "INSERT INTO saved_reviews (id, media_item_id, media_source_id, name, "
                "created_at, updated_at) "
                "VALUES (?, 'first', 'source', ?, '2026-01-01', '2026-01-01')",
                [("saved", "Original review"), ("mixed", "Mixed provenance")],
            )
            db.exec_driver_sql(
                "INSERT INTO review_scan_memberships (review_id, scan_id, role, position) "
                "VALUES (?, ?, ?, ?)",
                [
                    ("saved", "original", "video", 0),
                    ("saved", "original-audio", "audio", 1),
                    ("mixed", "original", "video", 0),
                    ("mixed", "changed", "audio", 1),
                ],
            )
            db.exec_driver_sql(
                "INSERT INTO media_item_exports (media_item_id, media_source_id, format, "
                "exported_at, review_id, exported_format_revision, scope, destination_path) "
                "VALUES ('first', 'source', 'edl', '2026-01-01', 'saved', 0, 'legacy', '/old.edl')"
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "revision",
    [
        "0031_explicit_filter_modes",
        "0033_scan_worker_timing",
        "0034_scan_read_indexes",
    ],
)
def test_supported_upgrades_preserve_artifacts_and_restore_original(tmp_path, revision):
    path = tmp_path / "scanner.db"
    _build(path, revision)
    with sqlite3.connect(path) as db:
        columns = _columns(db)
        original = _rows(db, columns)

    generation = adoption.upgrade_existing_database(path, tmp_path)

    validate_safety_snapshot(generation)
    recovered = adoption.export_upgrade_recovery(path, tmp_path, tmp_path / "recovered.db")
    assert adoption._inspect_database(recovered) == revision
    with sqlite3.connect(recovered) as db:
        assert _rows(db, columns) == original
    with sqlite3.connect(path) as db:
        assert _rows(db, columns) == original
        titles = dict(db.execute("SELECT media_item_id, title_id FROM media_title_sources"))
        assert titles["first"] == titles["replacement"] != titles["tv"]
        assert "archived" not in titles
        assert db.execute(
            "SELECT * FROM media_title_aliases ORDER BY provider, entity_type"
        ).fetchall() == [
            ("tmdb", "movie", "567", titles["first"]),
            ("tvdb", "aired_episode", "44:2.6", titles["tv"]),
            ("tvdb", "movie", "44", titles["first"]),
        ]
        scan_versions = dict(db.execute("SELECT scan_id, version_id FROM scan_media_versions"))
        assert scan_versions["original"] == scan_versions["original-audio"]
        assert (
            len({scan_versions["original"], scan_versions["changed"], scan_versions["other-item"]})
            == 3
        )
        assert "queued-scan" not in scan_versions
        assert "archived-scan" not in scan_versions
        reviews = dict(db.execute("SELECT review_id, version_id FROM review_media_versions"))
        assert reviews["saved"] == scan_versions["original"]
        assert reviews["mixed"] not in set(scan_versions.values())
        assert db.execute(
            "SELECT count(*) FROM media_versions WHERE content_sha256 IS NOT NULL"
        ).fetchone() == (0,)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert adoption.upgrade_existing_database(path, tmp_path) is None
