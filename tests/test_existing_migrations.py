"""File-backed admission, upgrade, and explicit recovery tests for CSS-05."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from scanner.persistence.migrations import adoption
from scanner.runtime.maintenance import MaintenanceUnavailable
from tests.account_migration_support import assert_preserved_after_account_migration
from tests.community_schema_fixture import create_unversioned_fixture
from tests.community_schema_support import baseline_specification


def _rows(path, columns=None):
    with sqlite3.connect(path) as connection:
        if columns is None:
            columns = _legacy_columns(path)
        rows = {}
        for name, names in columns.items():
            selected = ", ".join('"' + column + '"' for column in names)
            rows[name] = connection.execute(
                f'SELECT {selected} FROM "{name}" ORDER BY rowid'
            ).fetchall()
        return rows


def _legacy_columns(path):
    with sqlite3.connect(path) as connection:
        return {
            name: [column[1] for column in connection.execute(f'PRAGMA table_info("{name}")')]
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version' ORDER BY name"
            )
        }


def _fixture(tmp_path):
    path = tmp_path / "known.db"
    create_unversioned_fixture(path).dispose()
    return path


def test_application_startup_allows_reconciliation_but_worker_admission_stays_closed(
    tmp_path, monkeypatch
):
    from scanner.persistence.migrations import preflight
    from scanner.persistence.migrations.runner import migrate_fresh_database

    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    hold = tmp_path / "restore-reconciliation.json"
    hold.write_text('{"queue_paused":true}')
    monkeypatch.setattr(preflight, "database_location", lambda: (path, tmp_path))
    before = _rows(path)
    assert preflight.prepare_database() == adoption.HEAD_REVISION
    assert _rows(path) == before
    assert hold.is_file()
    with pytest.raises(RuntimeError, match="reconciliation before worker admission"):
        preflight.require_supported_schema()


def test_adoption_preserves_all_rows_and_matches_fresh_schema(tmp_path):
    path = _fixture(tmp_path)
    original = _rows(path)
    legacy_columns = _legacy_columns(path)
    generation = adoption.upgrade_existing_database(path, tmp_path)
    assert generation.is_dir()
    assert_preserved_after_account_migration(path, original, legacy_columns)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION

    engine = create_engine(f"sqlite:///{path}")
    try:
        actual = baseline_specification(engine)
        assert actual["tables"].pop("alembic_version")
        assert "scan_stage_coverage" in actual["tables"]
        with engine.connect() as connection:
            from scanner.persistence.migrations.schema import verify_current_schema

            verify_current_schema(connection)
        assert "alembic_version" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    assert adoption.upgrade_existing_database(path, tmp_path) is None
    assert_preserved_after_account_migration(path, original, legacy_columns)
    recovered = adoption.export_upgrade_recovery(path, tmp_path, tmp_path / "export.db")
    assert _rows(recovered) == original
    assert adoption._inspect_database(recovered) is None
    with pytest.raises(FileExistsError):
        adoption.export_upgrade_recovery(path, tmp_path, recovered)


@pytest.mark.parametrize(
    "revision",
    [
        "0001_current_baseline",
        "0002_scan_provenance",
        "0003_local_community",
        "0004_media_identity",
        "0005_audio_tracks",
        "0006_community_evidence",
        "0007_community_publications",
        "0008_api_key",
        "0009_readable_api_key",
    ],
)
def test_previous_revision_preserves_rows_and_recoverable_snapshot(tmp_path, revision):
    path = _fixture(tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            adoption._stamp(connection)
            configuration = Config()
            configuration.set_main_option(
                "script_location", str(adoption.Path(adoption.__file__).parent)
            )
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, revision)
    finally:
        engine.dispose()
    if revision in {
        "0003_local_community",
        "0004_media_identity",
        "0005_audio_tracks",
        "0006_community_evidence",
        "0007_community_publications",
        "0008_api_key",
        "0009_readable_api_key",
    }:
        with sqlite3.connect(path) as database:
            database.execute("UPDATE scans SET origin='imported' WHERE id='legacy-scan-001'")
            database.execute(
                "INSERT INTO community_connections "
                "(service_url, contributor_id, installation_id, key_id) "
                "VALUES ('https://community.example', 'reader', "
                "'installation-reader', 'reader-key')"
            )
            database.execute(
                "INSERT INTO community_imports (id, scan_id, service_url, contribution_id, "
                "revision_id, digest, author_id, artifact_type) "
                "VALUES ('legacy-import', 'legacy-scan-001', 'https://community.example', "
                "'root', 'revision', 'sha256:123', 'author', 'scan')"
            )
            database.execute(
                "INSERT INTO community_operations (operation_id, service_url, kind, state, "
                "contributor_id, installation_id, "
                "contribution_id, revision_id, digest, scan_id, receipt, updated_at) VALUES "
                "('original-operation', 'https://community.example', 'receipt', 'submitted', "
                "'reader', 'installation-reader', 'root', 'revision', 'sha256:123', "
                "'legacy-scan-001', 'original-receipt', '2026-10-03')"
            )
    if revision == "0008_api_key":
        with sqlite3.connect(path) as database:
            database.execute(
                "INSERT INTO api_keys (id, token_digest, created_at) VALUES (1, ?, ?)",
                ("a" * 64, "2026-10-13"),
            )
    if revision == "0009_readable_api_key":
        with sqlite3.connect(path) as database:
            database.execute(
                "INSERT INTO api_keys (id, token_digest, created_at, token_value) "
                "VALUES (1, ?, ?, ?)",
                ("a" * 64, "2026-10-13", "b" * 64),
            )
    original = _rows(path)
    columns = _legacy_columns(path)
    assert adoption._inspect_database(path) == revision
    generation = adoption.upgrade_existing_database(path, tmp_path)
    assert generation.is_dir()
    assert_preserved_after_account_migration(path, original, columns)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM media_items WHERE confirmed_identity_json IS NOT NULL"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM scans WHERE requested_audio_selection_json IS NOT NULL "
            "OR analyzed_audio_selection_json IS NOT NULL"
        ).fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM audio_track_selections").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM community_import_targets").fetchone() == (
            0,
        )
        assert connection.execute("SELECT count(*) FROM community_publications").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM community_imports WHERE artifact_bytes IS NOT NULL "
            "OR manifest_bytes IS NOT NULL OR signature_bytes IS NOT NULL "
            "OR historical_key_json IS NOT NULL OR provenance_json IS NOT NULL "
            "OR event_ids_json IS NOT NULL OR receipt_json IS NOT NULL"
        ).fetchone() == (0,)
    restored = adoption.export_upgrade_recovery(path, tmp_path, tmp_path / "previous-export.db")
    assert adoption._inspect_database(restored) == revision
    assert _rows(restored) == original


def test_publication_upgrade_preserves_populated_signed_evidence(tmp_path):
    path = _fixture(tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            adoption._stamp(connection)
            configuration = Config()
            configuration.set_main_option(
                "script_location", str(adoption.Path(adoption.__file__).parent)
            )
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "0006_community_evidence")
    finally:
        engine.dispose()
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute(
            "INSERT INTO community_imports (id, scan_id, service_url, contribution_id, "
            "revision_id, "
            "digest, author_id, artifact_type, artifact_bytes, manifest_bytes, signature_bytes, "
            "historical_key_json, provenance_json, event_ids_json, receipt_json) "
            "VALUES ('signed-import', 'legacy-scan-001', 'https://community.example', 'root', "
            "'revision', 'digest', 'author', 'scan', ?, ?, ?, ?, ?, ?, ?)",
            (
                b"exact-artifact",
                b"exact-manifest",
                b"exact-envelope",
                "historical-key",
                "provenance",
                "event-ids",
                "original-receipt",
            ),
        )
        database.execute(
            "INSERT INTO community_import_targets (import_id, media_item_id, media_source_id, "
            "resolved_local_path, target_evidence_json, review_required) "
            "SELECT 'signed-import', media_item_id, '', '/local/approved.mkv', '{}', 1 "
            "FROM scans WHERE id='legacy-scan-001'"
        )
    original, columns = _rows(path), _legacy_columns(path)
    assert adoption.upgrade_existing_database(path, tmp_path).is_dir()
    assert_preserved_after_account_migration(path, original, columns)
    recovered = adoption.export_upgrade_recovery(path, tmp_path, tmp_path / "signed-recovery.db")
    assert _rows(recovered) == original
    assert adoption._inspect_database(recovered) == "0006_community_evidence"
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION


@pytest.mark.parametrize("mutation", ["table", "trigger", "future", "corrupt", "foreign_key"])
def test_unsupported_database_is_not_stamped_or_snapshotted(tmp_path, mutation):
    path = _fixture(tmp_path)
    if mutation == "corrupt":
        path.write_bytes(b"not a SQLite database")
    else:
        with sqlite3.connect(path) as connection:
            if mutation == "table":
                connection.execute("CREATE TABLE mystery (id INTEGER)")
            elif mutation == "trigger":
                connection.execute(
                    "CREATE TRIGGER mystery AFTER INSERT ON scans BEGIN SELECT 1; END"
                )
            elif mutation == "future":
                connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
                connection.execute("INSERT INTO alembic_version VALUES ('future_revision')")
            else:
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute(
                    "INSERT INTO review_scan_memberships "
                    "(review_id, scan_id, role, position) "
                    "VALUES ('missing', 'missing', 'audio', 99)"
                )
    original = path.read_bytes()
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        adoption.upgrade_existing_database(path, tmp_path)
    assert path.read_bytes() == original
    assert not (tmp_path / adoption.JOURNAL_NAME).exists()
    snapshot_root = tmp_path / "migration-snapshots"
    assert not snapshot_root.exists() or not list(snapshot_root.glob("snapshot-*"))


@pytest.mark.parametrize("stage", ["before_stamp", "after_stamp", "after_rewrite"])
def test_failed_upgrade_retains_recoverable_original(tmp_path, monkeypatch, stage):
    path = _fixture(tmp_path)
    original = _rows(path)
    legacy_columns = _legacy_columns(path)
    real_stamp = adoption._stamp

    def fail_after_stamp(connection):
        if stage != "before_stamp":
            real_stamp(connection)
        if stage == "after_rewrite":
            connection.exec_driver_sql("ALTER TABLE media_items RENAME TO changed_media_items")
        raise RuntimeError("injected failure")

    monkeypatch.setattr(adoption, "_stamp", fail_after_stamp)
    with pytest.raises(RuntimeError, match="injected failure"):
        adoption.upgrade_existing_database(path, tmp_path)
    state = json.loads((tmp_path / adoption.JOURNAL_NAME).read_text())
    assert state["status"] == "failed"
    with pytest.raises(RuntimeError, match="unresolved"):
        adoption.upgrade_existing_database(path, tmp_path)
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    recovered = adoption.export_upgrade_recovery(path, tmp_path, recovery_dir / "export.db")
    assert _rows(recovered) == original
    assert adoption._inspect_database(recovered) is None
    monkeypatch.setattr(adoption, "_stamp", real_stamp)
    assert adoption.upgrade_existing_database(recovered, recovery_dir).is_dir()
    assert_preserved_after_account_migration(recovered, original, legacy_columns)
    assert adoption._inspect_database(recovered) == adoption.HEAD_REVISION
    assert json.loads((tmp_path / adoption.JOURNAL_NAME).read_text())["status"] == "failed"


def test_snapshot_failure_never_stamps(tmp_path, monkeypatch):
    path = _fixture(tmp_path)
    original = path.read_bytes()

    def fail_snapshot(*_args, **_kwargs):
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr(adoption, "create_safety_snapshot", fail_snapshot)
    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        adoption.upgrade_existing_database(path, tmp_path)
    assert path.read_bytes() == original
    assert not (tmp_path / adoption.JOURNAL_NAME).exists()


def test_upgrade_snapshot_includes_committed_wal_rows(tmp_path):
    path = _fixture(tmp_path)
    with sqlite3.connect(path) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute(
            "UPDATE saved_reviews SET name='Committed in WAL' WHERE id='review-legacy-001'"
        )
        writer.commit()
        generation = adoption.upgrade_existing_database(path, tmp_path)
        with sqlite3.connect(generation / "snapshot.sqlite3") as backup:
            assert backup.execute(
                "SELECT name FROM saved_reviews WHERE id='review-legacy-001'"
            ).fetchone() == ("Committed in WAL",)
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION


def test_competing_upgrade_cannot_stamp_or_snapshot(tmp_path, monkeypatch):
    path = _fixture(tmp_path)
    entered, release = Event(), Event()
    real_stamp = adoption._stamp

    def wait_at_stamp(connection):
        entered.set()
        assert release.wait(5)
        real_stamp(connection)

    monkeypatch.setattr(adoption, "_stamp", wait_at_stamp)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(adoption.upgrade_existing_database, path, tmp_path)
        try:
            assert entered.wait(5)
            with pytest.raises(MaintenanceUnavailable):
                adoption.upgrade_existing_database(path, tmp_path)
        finally:
            release.set()
        assert first.result().is_dir()
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
