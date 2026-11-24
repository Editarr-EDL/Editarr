"""Upgrade to 0037 keeps community-imported scans and archives revision-link data.

Starting points: the frozen baseline-v1 schema, 0013_community_identities with its
populated community fixture, and 0036_sampled_media_identity with community imports,
targets, and operations of every kind and state.
"""

import sqlite3

import pytest
from alembic import command
from sqlalchemy import create_engine

from scanner.backup.snapshots import validate_safety_snapshot
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import run_packaged, upgrade_connection
from scanner.persistence.migrations.schema import verify_versioned_schema
from tests.activity_migration_support import _upgrade
from tests.community_identity_migration_support import PERSONAL_FILTERS, build_community_database
from tests.community_identity_migration_support import no_network as no_network
from tests.community_schema_fixture import create_unversioned_fixture

SOURCE = "0036_sampled_media_identity"
KINDS = ("publish", "receipt", "vote")
STATES = ("pending", "submitted", "acknowledged", "failed")
_TIME = "2026-01-01"
_SCAN = (
    "INSERT INTO scans (id, media_item_id, scan_number, source_path, resolved_local_path, "
    "status, scan_audio, scan_video, speed_up, qwen_model, qwen_summaries_per_segment, "
    "qwen_segment_join_gap_seconds, selection_revision, visual_checkpoint_complete, "
    "audio_extraction_checkpoint_complete, audio_checkpoint_complete, checkpoint_stage, "
    "checkpoint_state, origin, created_at, updated_at) VALUES (?, 'item', ?, '/m.mkv', "
    "'/m.mkv', 'complete', 1, 0, 0, 'model', 4, 3.0, 0, 0, 0, 0, 'queued', '{}', ?, ?, ?)"
)


def _table(database, name):
    return database.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()


def _scans(path, columns=None, where=""):
    """Scans and findings, restricted to `columns` (each table's names) and the scans
    matching `where`, when given."""
    with sqlite3.connect(path) as database:
        if columns is None:
            columns = {
                name: [row[1] for row in database.execute(f"PRAGMA table_info({name})")]
                for name in ("scans", "scan_findings")
            }
        scans = f"SELECT id FROM scans {where}"
        return columns, {
            name: database.execute(
                f"SELECT {', '.join(names)} FROM {name} "
                f"WHERE {'id' if name == 'scans' else 'scan_id'} IN ({scans}) ORDER BY rowid"
            ).fetchall()
            for name, names in columns.items()
        }


def _seed_0036(path):
    """Build 0036 with an imported scan, its import and target, and every operation."""
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
                "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
                "created_at, updated_at) VALUES ('item', 'server', 'j', 'Movie', 'M', ?, ?)",
                (_TIME, _TIME),
            )
            db.exec_driver_sql(
                _SCAN,
                [
                    ("imported", 1, "imported", _TIME, _TIME),
                    ("generated", 2, "generated", _TIME, _TIME),
                ],
            )
            db.exec_driver_sql(
                "INSERT INTO scan_findings (scan_id, start_seconds, end_seconds, source, "
                "selected, edit_action, created_at, label) VALUES (?, ?, ?, 'audio', 1, 'mute', "
                "?, ?)",
                [
                    ("imported", 10.0, 12.0, _TIME, "imported finding"),
                    ("generated", 20.0, 21.0, _TIME, "model finding"),
                ],
            )
            db.exec_driver_sql(
                "INSERT INTO community_imports (id, scan_id, service_url, contribution_id, "
                "revision_id, digest, author_id, artifact_type, artifact_bytes, manifest_bytes, "
                "signature_bytes, historical_key_json, provenance_json, event_ids_json, "
                "receipt_json) VALUES ('import-1', 'imported', 'https://c.example', 'root', "
                "'revision', 'sha256:1', 'author', 'filter-set', ?, ?, ?, '{}', '{}', '[]', '{}')",
                (b"\x1f\x8b artifact", b'{"manifest":1}', b"\x00signature"),
            )
            db.exec_driver_sql(
                "INSERT INTO community_import_targets (import_id, media_item_id, "
                "media_source_id, resolved_local_path, video_hash, target_evidence_json, "
                "review_required) VALUES ('import-1', 'item', 'source', '/m.mkv', 'h', '{}', 0)"
            )
            for kind in KINDS:
                for state in STATES:
                    db.exec_driver_sql(
                        "INSERT INTO community_operations (operation_id, service_url, kind, "
                        "state, contributor_id, revision_id, digest, scan_id, consent_version, "
                        "receipt, vote, updated_at, owner_id, actor_id) VALUES (?, "
                        "'https://c.example', ?, ?, 'c', 'revision', 'sha256:1', ?, ?, ?, ?, "
                        "?, 1, 1)",
                        (
                            f"{kind}-{state}",
                            kind,
                            state,
                            "generated" if kind == "publish" else None,
                            "consent" if kind == "publish" else None,
                            "receipt" if kind == "receipt" else None,
                            "like" if kind == "vote" else None,
                            _TIME,
                        ),
                    )
            for state in STATES:
                db.exec_driver_sql(
                    "INSERT INTO community_publications (operation_id, artifact_bytes, "
                    "manifest_bytes, signature_bytes, manifest_digest, signature_digest, "
                    "preview_digest, consent_token, author_key_id, source_kind, source_id, "
                    "cancel_requested, error_code) VALUES (?, ?, 'm', 's', 'a', 'b', 'c', "
                    "'d', 'k', 'scan', 'generated', 0, ?)",
                    (
                        f"publish-{state}",
                        b"artifact-" + state.encode(),
                        "earlier" if state == "failed" else None,
                    ),
                )
    finally:
        engine.dispose()


def _data_path(tmp_path):
    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700)
    return path


def _upgrade_with_backup(path, tmp_path):
    with sqlite3.connect(path) as database:
        tables = [
            name
            for (name,) in database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        original = {name: _table(database, name) for name in tables}
    revision = adoption._inspect_database(path)
    generation = adoption.upgrade_existing_database(path, path.parent)
    validate_safety_snapshot(generation)
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert adoption._inspect_database(recovered) == revision
    with sqlite3.connect(recovered) as database:
        assert {name: _table(database, name) for name in tables} == original
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    return original


def test_head_is_0037():
    assert adoption.HEAD_REVISION == "0037_community_master_sets"


def test_baseline_v1_upgrade_keeps_scans_and_adds_empty_master_tables(tmp_path):
    path = _data_path(tmp_path)
    create_unversioned_fixture(path).dispose()
    columns, scans = _scans(path)
    _upgrade_with_backup(path, tmp_path)
    assert _scans(path, columns)[1] == scans
    with sqlite3.connect(path) as database:
        for table in ("community_master_links", "retired_community_imports"):
            assert _table(database, table) == []


def test_0013_upgrade_retires_receipts_and_votes_and_keeps_publications(tmp_path):
    path = _data_path(tmp_path)
    build_community_database(path, tmp_path / "media", PERSONAL_FILTERS)
    _upgrade(path, "0013_community_identities")
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT INTO scan_findings (scan_id, start_seconds, end_seconds, source, selected, "
            "edit_action, created_at) VALUES ('imported-scan-003', 5.0, 6.0, 'audio', 1, "
            "'mute', ?)",
            (_TIME,),
        )
    # Earlier revisions cancel the queued scan of archived media; imported scans stay.
    columns, scans = _scans(path, where="WHERE origin = 'imported'")
    assert scans["scans"] and scans["scan_findings"]
    original = _upgrade_with_backup(path, tmp_path)
    assert _scans(path, columns, "WHERE origin = 'imported'")[1] == scans
    with sqlite3.connect(path) as database:
        assert _table(database, "retired_community_imports") == original["community_imports"]
        retired = database.execute(
            "SELECT operation_id, kind, retired_reason FROM retired_community_operations "
            "ORDER BY operation_id"
        ).fetchall()
        assert retired == [
            ("op-receipt", "receipt", "revision_protocol_removed"),
            ("op-vote", "vote", "revision_protocol_removed"),
        ]
        # 0025 already ended this publication of archived media; 0037 leaves it as it was.
        assert database.execute(
            "SELECT o.operation_id, o.state, p.error_code, p.artifact_bytes "
            "FROM community_operations o JOIN community_publications p USING (operation_id)"
        ).fetchall() == [("op-publish", "failed", None, b"\x1f\x8b artifact")]


def test_0036_upgrade_keeps_imported_scans_and_every_legacy_row(tmp_path):
    path = _data_path(tmp_path)
    _seed_0036(path)
    scans = _scans(path)
    original = _upgrade_with_backup(path, tmp_path)
    assert _scans(path) == scans
    with sqlite3.connect(path) as database:
        assert _table(database, "retired_community_imports") == original["community_imports"]
        assert (
            _table(database, "retired_community_import_targets")
            == original["community_import_targets"]
        )
        assert {
            row[2] for row in database.execute("PRAGMA foreign_key_list(retired_community_imports)")
        } == {"scans"}
        assert {
            row[2]
            for row in database.execute("PRAGMA foreign_key_list(retired_community_import_targets)")
        } == {"media_items", "retired_community_imports"}
        operations = {row[0]: row for row in original["community_operations"]}
        retired = database.execute(
            "SELECT * FROM retired_community_operations ORDER BY operation_id"
        ).fetchall()
        assert [row[:-2] for row in retired] == [
            operations[key] for key in sorted(operations) if not key.startswith("publish")
        ]
        assert {row[-1] for row in retired} == {"revision_protocol_removed"}
        kept = database.execute(
            "SELECT operation_id, state, community_version_id, filter_id, receipt_id "
            "FROM community_operations ORDER BY operation_id"
        ).fetchall()
        assert kept == [
            ("publish-acknowledged", "acknowledged", None, None, None),
            ("publish-failed", "failed", None, None, None),
            ("publish-pending", "failed", None, None, None),
            ("publish-submitted", "failed", None, None, None),
        ]
        assert database.execute(
            "SELECT operation_id, error_code, artifact_bytes FROM community_publications "
            "ORDER BY operation_id"
        ).fetchall() == [
            ("publish-acknowledged", None, b"artifact-acknowledged"),
            ("publish-failed", "earlier", b"artifact-failed"),
            ("publish-pending", "protocol_retired", b"artifact-pending"),
            ("publish-submitted", "protocol_retired", b"artifact-submitted"),
        ]
        database.execute("PRAGMA foreign_keys = ON")
        database.execute("DELETE FROM scan_findings WHERE scan_id = 'imported'")
        database.execute("DELETE FROM scans WHERE id = 'imported'")
        assert _table(database, "retired_community_imports") == []
        assert _table(database, "retired_community_import_targets") == []


def _downgrade(path):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            run_packaged(command.downgrade, connection, SOURCE)
            verify_versioned_schema(connection, SOURCE)
    finally:
        engine.dispose()


def test_downgrade_restores_retired_rows_when_nothing_would_be_lost(tmp_path):
    path = tmp_path / "scanner.db"
    _seed_0036(path)
    with sqlite3.connect(path) as database:
        database.execute("DELETE FROM community_publications")
        database.execute("DELETE FROM community_operations WHERE kind = 'publish'")
        original = {
            name: _table(database, name)
            for name in ("community_operations", "community_imports", "community_import_targets")
        }
    _upgrade(path, adoption.HEAD_REVISION)
    _downgrade(path)
    with sqlite3.connect(path) as database:
        restored = {name: _table(database, name) for name in original}
    assert {name: sorted(rows) for name, rows in restored.items()} == {
        name: sorted(rows) for name, rows in original.items()
    }


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE community_publications SET error_code = 'protocol_retired'",
        "INSERT INTO community_master_preferences (owner_id, confidence_threshold, "
        "show_low_confidence) VALUES (1, 0.5, 1)",
    ],
)
def test_downgrade_refuses_to_discard_master_or_retired_publication_data(tmp_path, statement):
    path = tmp_path / "scanner.db"
    _seed_0036(path)
    with sqlite3.connect(path) as database:
        database.execute(
            "DELETE FROM community_publications WHERE operation_id != 'publish-failed'"
        )
        database.execute(
            "DELETE FROM community_operations WHERE kind = 'publish' "
            "AND operation_id != 'publish-failed'"
        )
    _upgrade(path, adoption.HEAD_REVISION)
    with sqlite3.connect(path) as database:
        database.execute(statement)
    with pytest.raises(RuntimeError, match="Restore a backup"):
        _downgrade(path)
