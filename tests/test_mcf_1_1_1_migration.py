"""The MCF 1.1.1 upgrade preserves earlier data and enables the new categories."""

import sqlite3

import pytest
from sqlalchemy import create_engine

from scanner.backup.snapshots import validate_safety_snapshot
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import upgrade_connection
from tests.explicit_filter_modes_support import legacy_instance_severities

pytestmark = pytest.mark.real_migrations

_SOURCE = "0040_review_community_filters"
_TIME = "2026-11-17 00:00:00"
_CATEGORIES = (
    ("discrimination", "sizeism"),
    ("discrimination", "ableism"),
    ("discrimination", "ageism"),
    ("dispensable_scenes", "other"),
    ("language", "innuendo"),
    ("sex", "sexualRape"),
    ("violence", "gore"),
    ("violence", "abuse"),
)
_CLASSIFIED_TABLES = (
    "scan_findings",
    "review_filters",
    "filter_selection_rules",
    "user_filter_copy_rules",
    "user_filter_rule_overrides",
    "user_filter_personal_rules",
)
_PRESERVED_TABLES = (
    *_CLASSIFIED_TABLES,
    "scan_mcf_documents",
    "review_policy_evidence",
    "user_filter_severity_overrides",
)


def _fixture(path):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            upgrade_connection(connection, _SOURCE)
    finally:
        engine.dispose()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            "INSERT INTO users (id, role, status, local_export_id, permission_revision, "
            "created_at, updated_at) VALUES (1, 'owner', 'active', ?, 1, ?, ?)",
            ("local-" + "e" * 32, _TIME, _TIME),
        )
        db.execute("INSERT INTO user_filter_preferences VALUES (1, 'copy', 3, 2, ?)", (_TIME,))
        db.execute("UPDATE mcf_severity_defaults SET severity = 'high' WHERE subcategory = 'rape'")
        db.execute(
            "INSERT INTO user_filter_copy_severities "
            "SELECT 1, subcategory, position, severity FROM mcf_severity_defaults"
        )
        db.execute(
            "UPDATE user_filter_copy_severities SET severity = 'low' WHERE subcategory = 'rape'"
        )
        db.execute("INSERT INTO user_filter_severity_overrides VALUES (1, 'rape', 'low')")
        for table, ids in (
            ("filter_selection_rules", "id, settings_id, position"),
            ("user_filter_copy_rules", "id, user_id, position"),
            ("user_filter_personal_rules", "id, user_id, position"),
            ("user_filter_rule_overrides", "rule_id, user_id, disabled"),
        ):
            db.execute(
                f"INSERT INTO {table} ({ids}, disposition, category, subcategory) "
                "VALUES ('rule', 1, 0, 'deny', 'violence', 'rape')"
            )
        db.execute(
            "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
            "created_at, updated_at) VALUES ('media', 'server', 'item', 'Movie', 'Film', ?, ?)",
            (_TIME, _TIME),
        )
        db.execute(
            "INSERT INTO scans (id, media_item_id, scan_number, source_path, resolved_local_path, "
            "status, scan_audio, scan_video, speed_up, qwen_model, qwen_summaries_per_segment, "
            "qwen_segment_join_gap_seconds, selection_revision, visual_checkpoint_complete, "
            "audio_extraction_checkpoint_complete, audio_checkpoint_complete, checkpoint_stage, "
            "checkpoint_state, created_at, updated_at) VALUES ('scan', 'media', 1, '/m', '/m', "
            "'complete', 0, 1, 0, 'q', 4, 3.0, 0, 0, 0, 0, 'queued', '{}', ?, ?)",
            (_TIME, _TIME),
        )
        db.execute(
            "INSERT INTO scan_findings (id, scan_id, source, start_seconds, end_seconds, selected, "
            "edit_action, created_at, category, subcategory) VALUES "
            "(7, 'scan', 'visual', 5, 6, 1, 'skip', ?, 'violence', 'rape')",
            (_TIME,),
        )
        db.execute(
            "INSERT INTO scan_mcf_documents (scan_id, title, source_version, "
            "material_start_seconds, material_end_seconds) VALUES ('scan', 'Film', '1.1.0', 0, 120)"
        )
        db.execute(
            "INSERT INTO saved_reviews (id, media_item_id, name, owner_id, original_author_id, "
            "created_at, updated_at) VALUES ('review', 'media', 'Review', 1, 1, ?, ?)",
            (_TIME, _TIME),
        )
        db.execute(
            "INSERT INTO review_filters (review_id, id, source_scan_id, source_finding_id, "
            "position, source, category, subcategory, start_seconds, end_seconds, edit_action, "
            "selected) VALUES ('review', 'filter', 'scan', 7, 0, 'visual', 'violence', 'rape', "
            "5, 6, 'skip', 0)"
        )
        db.execute("INSERT INTO review_policy_evidence VALUES ('review', 7, 5, 6, 0, 120, 0)")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def _rows(db):
    return {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in _PRESERVED_TABLES}


def _indexes(db):
    return db.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'index' ORDER BY name"
    ).fetchall()


def test_upgrade_preserves_0040_rows_backup_and_indexes(tmp_path):
    path = tmp_path / "scanner.db"
    _fixture(path)
    with sqlite3.connect(path) as db:
        original = _rows(db)
        indexes = _indexes(db)
        old_defaults = db.execute(
            "SELECT * FROM mcf_severity_defaults ORDER BY position"
        ).fetchall()
        old_copy = db.execute(
            "SELECT * FROM user_filter_copy_severities ORDER BY position"
        ).fetchall()

    generation = adoption.upgrade_existing_database(path, tmp_path)
    validate_safety_snapshot(generation)
    recovered = adoption.export_upgrade_recovery(path, tmp_path, tmp_path / "recovered.db")
    assert adoption._inspect_database(recovered) == _SOURCE
    with sqlite3.connect(recovered) as db:
        assert _rows(db) == original
        assert db.execute("SELECT * FROM mcf_severity_defaults ORDER BY position").fetchall() == (
            old_defaults
        )
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    with sqlite3.connect(path) as db:
        assert _rows(db) == original
        assert _indexes(db) == indexes
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        defaults = db.execute("SELECT * FROM mcf_severity_defaults ORDER BY position").fetchall()
        assert legacy_instance_severities(defaults) == old_defaults
        assert db.execute(
            "SELECT * FROM user_filter_copy_severities ORDER BY position"
        ).fetchall() == old_copy + [
            (1, subcategory, position, "medium")
            for position, (_family, subcategory) in enumerate(_CATEGORIES, len(old_copy))
        ]
        for table in _CLASSIFIED_TABLES:
            statement = f"UPDATE {table} SET category = ?, subcategory = ?"
            for family, subcategory in _CATEGORIES:
                assert db.execute(statement, (family, subcategory)).rowcount == 1
                wrong_family = "language" if family != "language" else "sex"
                with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                    db.execute(statement, (wrong_family, subcategory))
        db.execute("UPDATE scan_mcf_documents SET source_version = '1.1.1'")
        with pytest.raises(sqlite3.IntegrityError, match="mcfsourceversion"):
            db.execute("UPDATE scan_mcf_documents SET source_version = '1.1.2'")
