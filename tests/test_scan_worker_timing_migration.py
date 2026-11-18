"""Upgrade existing scan rows with recoverable data and conservative timing."""

import sqlite3
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.backup.snapshots import validate_safety_snapshot
from scanner.db import ScanRow
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import upgrade_connection
from scanner.persistence.repository.repository_queue import mark_orphaned_scan_workers


def test_upgrade_preserves_scan_records_and_bounds_interrupted_time(tmp_path):
    path = tmp_path / "scanner.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            upgrade_connection(connection, "0032_manual_filter_tracking")
            connection.exec_driver_sql(
                "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
                "created_at, updated_at) VALUES ('media', 'server', 'item', 'Movie', 'Film', "
                "'2026-11-17', '2026-11-17')"
            )
            connection.exec_driver_sql(
                "INSERT INTO scans (id, media_item_id, scan_number, source_path, "
                "resolved_local_path, status, scan_audio, scan_video, speed_up, qwen_model, "
                "qwen_summaries_per_segment, qwen_segment_join_gap_seconds, selection_revision, "
                "visual_checkpoint_complete, audio_extraction_checkpoint_complete, "
                "audio_checkpoint_complete, checkpoint_stage, checkpoint_state, "
                "worker_started_at, worker_heartbeat_at, created_at, updated_at) "
                "VALUES (?, 'media', ?, '/film', '/film', ?, 1, 0, 0, "
                "'qwen3-vl-8b-instruct-4bit', 4, 3.0, 0, 0, 0, 0, 'queued', '{}', ?, ?, "
                "'2026-11-17 00:00:00', ?)",
                [
                    ("done", 1, "complete", "2026-11-17 00:00:00", None, "2026-11-17 00:00:45"),
                    (
                        "paused",
                        2,
                        "orphaned",
                        "2026-11-17 00:00:00",
                        "2026-11-17 00:00:20",
                        "2026-11-17 01:00:00",
                    ),
                    (
                        "active",
                        3,
                        "scanning",
                        "2026-11-17 00:00:00",
                        "2026-11-17 00:00:30",
                        "2026-11-17 00:00:30",
                    ),
                    ("new", 4, "queued", None, None, "2026-11-17 00:00:00"),
                ],
            )
            connection.exec_driver_sql(
                "INSERT INTO scan_queue (scan_id, sort_order, created_at, updated_at) "
                "VALUES ('active', 1, '2026-11-17', '2026-11-17')"
            )

        generation = adoption.upgrade_existing_database(path, tmp_path)
        validate_safety_snapshot(generation)
        with sqlite3.connect(generation / "snapshot.sqlite3") as backup:
            assert backup.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "0032_manual_filter_tracking",
            )
            assert backup.execute("SELECT count(*) FROM scans").fetchone() == (4,)

        with Session(engine) as db:
            assert db.get(ScanRow, "done").worker_elapsed_seconds == 45
            assert db.get(ScanRow, "paused").worker_elapsed_seconds == 20
            assert db.get(ScanRow, "paused").worker_attempt_started_at is None
            assert db.get(ScanRow, "new").worker_elapsed_seconds is None
            assert db.get(ScanRow, "active").worker_attempt_started_at == datetime(2026, 11, 17)
            (recovered,) = mark_orphaned_scan_workers(db, set())
            assert recovered.status == "orphaned"
            assert recovered.scan_duration_seconds == 30
            assert recovered.scan_duration_as_of is None
    finally:
        engine.dispose()
