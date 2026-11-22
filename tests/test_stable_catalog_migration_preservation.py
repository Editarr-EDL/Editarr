"""Upgrade populated databases without moving historical filters to a replacement cut."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from scanner.backup.snapshots import validate_safety_snapshot
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import upgrade_connection

_ORIGINAL_ID = "566adf50c1bc3752f7f670313d6e7445"
_LIBRARY_ID = "f137a2dd21bbc1b99aa5c0f6bf02a805"
_ORIGINAL_HASH = "a" * 32
_EDL = b"1.75\t2.125\t1\n"
_LEGACY_TABLES = (
    "media_items",
    "scans",
    "scan_findings",
    "saved_reviews",
    "review_scan_memberships",
    "review_filters",
    "media_item_exports",
    "media_item_export_scans",
)


def _seed_history(connection, path: Path, sidecar: Path) -> None:
    connection.exec_driver_sql(
        "INSERT INTO media_items (id, server_id, jellyfin_id, library_id, item_type, name, "
        "path, created_at, updated_at) "
        "VALUES ('historic-media', 'server', ?, ?, 'Movie', 'The Life of Chuck', "
        "?, '2026-11-17', '2026-11-17')",
        (_ORIGINAL_ID, _LIBRARY_ID, str(path)),
    )
    connection.exec_driver_sql(
        "INSERT INTO scans (id, media_item_id, scan_number, source_path, resolved_local_path, "
        "media_source_id, media_provider_id, video_hash, duration_seconds, status, scan_audio, "
        "scan_video, speed_up, qwen_model, qwen_summaries_per_segment, "
        "qwen_segment_join_gap_seconds, selection_revision, visual_checkpoint_complete, "
        "audio_extraction_checkpoint_complete, audio_checkpoint_complete, checkpoint_stage, "
        "checkpoint_state, created_at, updated_at) "
        "VALUES (?, 'historic-media', ?, ?, ?, ?, 'tmdb:842924', ?, 6644.471, 'complete', ?, "
        "?, 0, 'qwen3-vl-8b-instruct-4bit', 4, 3.0, 0, 0, 0, 0, 'queued', '{}', "
        "'2026-11-17', '2026-11-17')",
        [
            (scan, number, str(path), str(path), _ORIGINAL_ID, _ORIGINAL_HASH, audio, video)
            for scan, number, audio, video in [("audio-scan", 1, 1, 0), ("video-scan", 2, 0, 1)]
        ],
    )
    connection.exec_driver_sql(
        "INSERT INTO scan_findings (id, scan_id, start_seconds, end_seconds, label, source, "
        "selected, edit_action, created_at) "
        "VALUES (5, 'audio-scan', 1.75, 2.125, 'Selected language', 'audio', 1, 'mute', "
        "'2026-11-17')"
    )
    connection.exec_driver_sql(
        "INSERT INTO saved_reviews (id, media_item_id, media_source_id, name, revision, "
        "content_revision, edl_content_revision, mcf_content_revision, created_at, updated_at) "
        "VALUES ('old-review', 'historic-media', ?, 'Original review', 3, 2, 2, 1, "
        "'2026-11-17', '2026-11-17')",
        (_ORIGINAL_ID,),
    )
    connection.exec_driver_sql(
        "INSERT INTO review_scan_memberships (review_id, scan_id, role, position) "
        "VALUES ('old-review', 'audio-scan', 'audio', 0), ('old-review', 'video-scan', 'video', 1)"
    )
    connection.exec_driver_sql(
        "INSERT INTO review_filters (review_id, id, source_scan_id, source_finding_id, position, "
        "source, label, start_seconds, end_seconds, edit_action, selected) "
        "VALUES ('old-review', 'filter-copy', 'audio-scan', 5, 0, 'audio', 'Selected language', "
        "1.8, 2.125, 'mute', 1)"
    )
    connection.exec_driver_sql(
        "INSERT INTO media_item_exports (id, media_item_id, media_source_id, format, exported_at, "
        "review_id, exported_format_revision, scope, content_bytes, destination_path) "
        "VALUES (7, 'historic-media', ?, 'edl', '2026-11-17', 'old-review', 2, 'legacy', ?, ?)",
        (_ORIGINAL_ID, _EDL, str(sidecar)),
    )
    connection.exec_driver_sql(
        "INSERT INTO media_item_export_scans (media_item_export_id, scan_id, role) "
        "VALUES (7, 'audio-scan', 'audio')"
    )


def _old_columns(database):
    return {
        table: tuple(row[1] for row in database.execute(f'PRAGMA table_info("{table}")'))
        for table in _LEGACY_TABLES
    }


def _old_rows(database, columns):
    rows = {}
    for table, names in columns.items():
        fields = ", ".join(f'"{column}"' for column in names)
        rows[table] = database.execute(f'SELECT {fields} FROM "{table}" ORDER BY rowid').fetchall()
    return rows


@pytest.mark.parametrize("revision", ["0031_explicit_filter_modes", "0034_scan_read_indexes"])
def test_upgrade_preserves_historical_copy_and_its_original_version(tmp_path, revision):
    original_file = tmp_path / "The Life of Chuck - Original.mkv"
    sidecar = original_file.with_suffix(".edl")
    sidecar.write_bytes(_EDL)

    path = tmp_path / "scanner.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            upgrade_connection(connection, revision)
            _seed_history(connection, original_file, sidecar)
    finally:
        engine.dispose()

    with closing(sqlite3.connect(path)) as database:
        old_columns = _old_columns(database)
        historical_rows = _old_rows(database, old_columns)
    generation = adoption.upgrade_existing_database(path, tmp_path)
    assert generation is not None
    validate_safety_snapshot(generation)
    with closing(sqlite3.connect(generation / "snapshot.sqlite3")) as snapshot:
        assert snapshot.execute("SELECT version_num FROM alembic_version").fetchone() == (revision,)
        assert _old_rows(snapshot, old_columns) == historical_rows

    with closing(sqlite3.connect(path)) as database:
        assert database.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
        assert _old_rows(database, old_columns) == historical_rows
        title = database.execute(
            "SELECT title_id FROM media_title_sources WHERE media_item_id = 'historic-media'"
        ).fetchone()[0]
        assert database.execute(
            "SELECT provider, entity_type, value FROM media_title_aliases WHERE title_id = ?",
            (title,),
        ).fetchall() == [("tmdb", "movie", "842924")]
        (version,) = database.execute(
            "SELECT id FROM media_versions WHERE title_id = ? AND video_hash = ? "
            "AND content_sha256 IS NULL",
            (title, _ORIGINAL_HASH),
        ).fetchone()
        assert database.execute(
            "SELECT media_item_id, media_source_id FROM media_version_locators "
            "WHERE version_id = ?",
            (version,),
        ).fetchall() == [("historic-media", _ORIGINAL_ID)]
        assert database.execute(
            "SELECT scan_id FROM scan_media_versions WHERE version_id = ? ORDER BY scan_id",
            (version,),
        ).fetchall() == [("audio-scan",), ("video-scan",)]
        assert database.execute(
            "SELECT review_id FROM review_media_versions WHERE version_id = ?",
            (version,),
        ).fetchall() == [("old-review",)]

    recovered_dir = tmp_path / "rehearsal"
    recovered = adoption.export_upgrade_recovery(path, tmp_path, recovered_dir / "scanner.db")
    assert adoption._inspect_database(recovered) == revision
    with closing(sqlite3.connect(recovered)) as database:
        assert _old_rows(database, old_columns) == historical_rows
    assert adoption.upgrade_existing_database(path, tmp_path) is None
    assert sidecar.read_bytes() == _EDL
