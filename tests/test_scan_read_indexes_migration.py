"""Upgrade 0033 databases with scan read indexes while keeping every scan row."""

import sqlite3

from sqlalchemy import create_engine

from scanner.backup.snapshots import validate_safety_snapshot
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import upgrade_connection

_INDEXES = {
    "ix_scan_findings_scan_start": ["scan_id", "start_seconds", "id"],
    "ix_scan_media_item_associations_media_item_id": ["media_item_id"],
}
_ROWS = {
    "scans": "SELECT id, media_item_id, scan_number, status FROM scans ORDER BY id",
    "scan_findings": "SELECT id, scan_id, start_seconds, label FROM scan_findings ORDER BY id",
    "scan_media_item_associations": (
        "SELECT scan_id, media_item_id FROM scan_media_item_associations ORDER BY id"
    ),
}


def _indexes(database):
    return {
        name: [column[2] for column in database.execute(f"PRAGMA index_info({name})")]
        for name in _INDEXES
        if database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
        ).fetchone()
    }


def _rows(database):
    return {table: database.execute(query).fetchall() for table, query in _ROWS.items()}


def test_upgrade_from_0033_adds_scan_read_indexes_and_preserves_rows(tmp_path):
    path = tmp_path / "scanner.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            upgrade_connection(connection, "0033_scan_worker_timing")
            connection.exec_driver_sql(
                "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
                "created_at, updated_at) VALUES (?, 'server', ?, 'Movie', ?, "
                "'2026-11-17', '2026-11-17')",
                [("media", "item", "Film"), ("other", "other", "Other")],
            )
            connection.exec_driver_sql(
                "INSERT INTO scans (id, media_item_id, scan_number, source_path, "
                "resolved_local_path, status, scan_audio, scan_video, speed_up, qwen_model, "
                "qwen_summaries_per_segment, qwen_segment_join_gap_seconds, selection_revision, "
                "visual_checkpoint_complete, audio_extraction_checkpoint_complete, "
                "audio_checkpoint_complete, checkpoint_stage, checkpoint_state, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, '/film', '/film', 'complete', 1, 0, 0, "
                "'qwen3-vl-8b-instruct-4bit', 4, 3.0, 0, 0, 0, 0, 'queued', '{}', "
                "'2026-11-17', '2026-11-17')",
                [("first", "media", 1), ("reused", "other", 1)],
            )
            connection.exec_driver_sql(
                "INSERT INTO scan_findings (scan_id, start_seconds, end_seconds, label, source, "
                "selected, edit_action, created_at) "
                "VALUES (?, ?, ?, ?, 'audio', 1, 'mute', '2026-11-17')",
                [("first", 9.0, 10.0, "late"), ("first", 1.0, 2.0, "early")],
            )
            connection.exec_driver_sql(
                "INSERT INTO scan_media_item_associations (scan_id, media_item_id, created_at) "
                "VALUES ('reused', 'media', '2026-11-17')"
            )
        with sqlite3.connect(path) as database:
            source = _rows(database)
            assert _indexes(database) == {}

        generation = adoption.upgrade_existing_database(path, tmp_path)

        validate_safety_snapshot(generation)
        with sqlite3.connect(generation / "snapshot.sqlite3") as backup:
            assert backup.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "0033_scan_worker_timing",
            )
            assert _rows(backup) == source
        assert adoption._inspect_database(path) == "0041_mcf_1_1_1"
        with sqlite3.connect(path) as database:
            assert _indexes(database) == _INDEXES
            assert _rows(database) == source
            plan = database.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM scan_findings WHERE scan_id = 'first' "
                "ORDER BY start_seconds, id"
            ).fetchall()
            assert "ix_scan_findings_scan_start" in " ".join(row[-1] for row in plan)
    finally:
        engine.dispose()
