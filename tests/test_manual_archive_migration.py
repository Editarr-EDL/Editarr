"""0026 archives every Other Files record from each supported schema and keeps every row."""

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.persistence.migrations import adoption
from scanner.persistence.repository.archive_records import read_archive
from scanner.persistence.rows.db_archive import ArchivedSource
from scanner.persistence.rows.db_catalog import MediaItemRow
from scanner.persistence.rows.db_scans import ScanRow
from tests import upgrade_matrix_support as state
from tests.activity_migration_support import _clone, _upgrade
from tests.explicit_filter_modes_support import drop_0031_additions
from tests.test_account_migration import _snapshot
from tests.test_upgrade_matrix import SOURCES as MATRIX_SOURCES
from tests.test_upgrade_matrix import _built

_JELLYFIN = "http://jellyfin.test"
_ACCESS = "SELECT 1 FROM sqlite_master WHERE name = 'media_access_exceptions'"
_OTHER_EXPORT, _JELLYFIN_EXPORT = 3, 4
SOURCES = MATRIX_SOURCES[: MATRIX_SOURCES.index("0026_archive_manual_files")]
_OTHER_FILES_SCOPE = "INSERT INTO media_access_scope_sources VALUES (1, 'manual_files', '')"


def _with_other_files(tmp_path, revision):
    """The matrix fixture with its item back in Other Files and one live Jellyfin item queued."""
    path = _built(tmp_path, revision)
    with sqlite3.connect(path) as database:
        (item, media) = database.execute("SELECT id, path FROM media_items").fetchone()
        database.execute(
            "UPDATE media_items SET server_id = 'editarr-manual', jellyfin_id = ?",
            (f"manual:{media}",),
        )
        _clone(
            database,
            "media_items",
            item,
            id="jellyfin-movie",
            server_id=_JELLYFIN,
            jellyfin_id="0" * 31 + "2",
            name="Live Movie",
        )
        _clone(
            database,
            "scans",
            "queued-scan-002",
            id="jellyfin-queued-scan",
            media_item_id="jellyfin-movie",
            scan_number=1,
        )
        (queue_id,) = database.execute("SELECT max(id) FROM scan_queue").fetchone()
        _clone(
            database,
            "scan_queue",
            queue_id,
            id=queue_id + 1,
            scan_id="jellyfin-queued-scan",
            sort_order=queue_id + 1,
        )
        _queue_jellyfin_regeneration(database)
        if database.execute(_ACCESS).fetchone():
            database.execute(
                "INSERT INTO media_access_exceptions VALUES "
                "(1, 'manual_files', 'editarr-manual', ?, '2026-01-01')",
                (media,),
            )
            database.execute(_OTHER_FILES_SCOPE)
    return path


def _queue_jellyfin_regeneration(database):
    """Give the live Jellyfin item a copy of the fixture's queued personal export."""
    exports = [row[1] for row in database.execute("PRAGMA table_info(media_item_exports)")]
    if "snapshot_json" not in exports:
        return
    _clone(
        database,
        "media_item_exports",
        _OTHER_EXPORT,
        id=_JELLYFIN_EXPORT,
        media_item_id="jellyfin-movie",
    )
    jobs = [
        row[1] for row in database.execute("PRAGMA table_info(media_item_export_regenerations)")
    ]
    if jobs:
        selected = ", ".join("?" if name == "export_id" else name for name in jobs)
        database.execute(
            f"INSERT INTO media_item_export_regenerations ({', '.join(jobs)}) "
            f"SELECT {selected} FROM media_item_export_regenerations WHERE export_id = ?",
            (_JELLYFIN_EXPORT, _OTHER_EXPORT),
        )


def _counts(rows):
    return {state.RENAMED.get(name, name): len(values) for name, values in rows.items()}


def _statuses(path):
    with sqlite3.connect(path) as database:
        return dict(database.execute("SELECT id, status FROM scans"))


def _regenerations(path):
    with sqlite3.connect(path) as database:
        if not database.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'media_item_export_regenerations'"
        ).fetchone():
            return []
        return database.execute(
            "SELECT export_id, state, due_at, failure_reason FROM media_item_export_regenerations"
        ).fetchall()


@pytest.mark.parametrize("revision", SOURCES)
def test_every_supported_revision_archives_other_files_and_keeps_every_row(tmp_path, revision):
    path = _with_other_files(tmp_path, revision)
    original, _ = _snapshot(path)
    before = _statuses(path)
    assert before["queued-scan-002"] == before["jellyfin-queued-scan"] == "queued"
    jobs = _regenerations(path)
    assert all(state == "queued" for _export, state, _due, _reason in jobs)

    generation = adoption.upgrade_existing_database(path, path.parent)

    assert generation.is_dir()
    assert (
        adoption._inspect_database(path) == adoption.HEAD_REVISION == "0037_community_master_sets"
    )
    upgraded, _ = _snapshot(path)
    drop_0031_additions(upgraded, original)
    after = _counts(upgraded)
    assert {name: after.get(name) for name in _counts(original)} == _counts(original) | {
        "scan_queue": 1
    }
    with sqlite3.connect(path) as database:
        assert sorted(database.execute("SELECT server_id, archived_source FROM media_items")) == [
            ("editarr-manual", "manual_files"),
            (_JELLYFIN, None),
        ]
        assert database.execute("SELECT scan_id FROM scan_queue").fetchall() == [
            ("jellyfin-queued-scan",)
        ]
        assert not database.execute(_ACCESS).fetchone()
    assert _statuses(path) == before | {"queued-scan-002": "cancelled"}
    stopped = {export: job for export, *job in _regenerations(path)}
    assert {export for export, *_ in jobs} <= set(stopped)
    if (revision or "") >= "0021_export_snapshots":
        assert stopped[_OTHER_EXPORT] == ["failed", None, "regeneration_failed"]
        state, due, reason = stopped.pop(_JELLYFIN_EXPORT)
        assert (state, due is not None, reason) == ("queued", True, None)
    assert set(stopped) <= {_OTHER_EXPORT}
    assert adoption.upgrade_existing_database(path, path.parent) is None
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert _snapshot(recovered)[0] == original


def test_access_rows_for_other_files_move_to_the_archive(tmp_path):
    path = _with_other_files(tmp_path, SOURCES[-1])
    with sqlite3.connect(path) as database:
        (media,) = database.execute(
            "SELECT path FROM media_items WHERE server_id = 'editarr-manual'"
        ).fetchone()
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        assert database.execute(
            "SELECT source_table, row_json FROM archived_media_access_rows ORDER BY id"
        ).fetchall()[-2:] == [
            (
                "media_access_scope_sources",
                '{"scope_id": 1, "source_id": "", "source_kind": "manual_files"}',
            ),
            (
                "media_access_exceptions",
                f'{{"granted_at": "2026-01-01", "item_key": "{media}", "server_key": '
                '"editarr-manual", "source_kind": "manual_files", "user_id": 1}',
            ),
        ]
        assert not database.execute(
            "SELECT 1 FROM media_access_scope_sources WHERE source_kind = 'manual_files'"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(_OTHER_FILES_SCOPE)


def test_the_archive_reads_former_other_files_and_live_reads_do_not(tmp_path):
    path = _with_other_files(tmp_path, SOURCES[-1])
    adoption.upgrade_existing_database(path, path.parent)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as db:
            (item,) = [
                entry
                for entry in read_archive(db, with_paths=True, offset=0, limit=100).items
                if entry.source is ArchivedSource.MANUAL_FILES
            ]
            assert item.title == "Movie.mkv"
            assert item.path.endswith("Movie.mkv")
            assert {scan.id for scan in item.scans} == {
                "legacy-scan-001",
                "queued-scan-002",
                "imported-scan-003",
            }
            assert item.reviews and item.exports
            assert [row.id for row in db.query(MediaItemRow)] == ["jellyfin-movie"]
            assert [row.id for row in db.query(ScanRow)] == ["jellyfin-queued-scan"]
    finally:
        engine.dispose()


def _clone_publication(database, operation_id, source_kind, source_id, scan_id, state):
    """Copy the fixture's pending publication under a new ID, source, and state."""
    for table in ("community_operations", "community_publications"):
        columns = [row[1] for row in database.execute(f"PRAGMA table_info({table})")]
        changes = {"operation_id": operation_id}
        if table == "community_operations":
            changes |= {"scan_id": scan_id, "state": state}
        else:
            changes |= {"source_kind": source_kind, "source_id": source_id}
        selected = ", ".join("?" if name in changes else name for name in columns)
        database.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) SELECT {selected} FROM {table} "
            "WHERE operation_id = 'op-publish'",
            [changes[name] for name in columns if name in changes],
        )


def test_unsent_publications_end_cancelled_and_sent_ones_keep_polling(tmp_path):
    path = _with_other_files(tmp_path, SOURCES[-1])
    with sqlite3.connect(path) as database:
        (review,) = database.execute("SELECT id FROM saved_reviews LIMIT 1").fetchone()
        scan = "legacy-scan-001"
        _clone_publication(database, "op-review", "review", review, None, "pending")
        _clone_publication(database, "op-sent", "scan", scan, scan, "submitted")
        _clone_publication(database, "op-done", "scan", scan, scan, "acknowledged")
        live = "jellyfin-queued-scan"
        _clone_publication(database, "op-live", "scan", live, live, "pending")
    # Stop at 0026: 0037 later retires every unfinished publication.
    _upgrade(path, "0026_archive_manual_files")
    with sqlite3.connect(path) as database:
        assert database.execute(
            "SELECT operation.operation_id, operation.state, publication.cancel_requested "
            "FROM community_operations AS operation JOIN community_publications AS publication "
            "USING (operation_id) ORDER BY operation_id"
        ).fetchall() == [
            ("op-done", "acknowledged", 0),
            ("op-live", "pending", 0),
            ("op-publish", "failed", 1),
            ("op-review", "failed", 1),
            ("op-sent", "submitted", 1),
        ]
