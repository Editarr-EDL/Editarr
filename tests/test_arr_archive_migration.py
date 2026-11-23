"""0025 archives every Radarr and Sonarr record from each supported schema and keeps every row."""

import sqlite3

import pytest

from scanner.persistence.migrations import adoption
from tests import upgrade_matrix_support as state
from tests.activity_migration_support import _clone
from tests.explicit_filter_modes_support import drop_0031_additions
from tests.stable_media_identity_migration_support import without_stable_media_identity
from tests.test_account_migration import _snapshot
from tests.test_upgrade_matrix import SOURCES as MATRIX_SOURCES
from tests.test_upgrade_matrix import _built

_RADARR = "radarr:http://radarr.test"
_SONARR = "sonarr:http://sonarr.test"
_UNFINISHED_JOBS = {"queued", "running", "withdrawing"}
SOURCES = MATRIX_SOURCES[: MATRIX_SOURCES.index("0025_archive_arr_sources")]


def _with_arr_records(tmp_path, revision):
    """The matrix fixture with its item moved to Radarr and one more scanned Sonarr episode."""
    path = _built(tmp_path, revision)
    with sqlite3.connect(path) as database:
        database.execute(
            "UPDATE media_items SET server_id = ?, jellyfin_id = '42', item_type = 'Movie'",
            (_RADARR,),
        )
        (item,) = database.execute("SELECT id FROM media_items").fetchone()
        _clone(
            database,
            "media_items",
            item,
            id="sonarr-episode",
            server_id=_SONARR,
            jellyfin_id="77",
            item_type="Episode",
            name="Pilot",
        )
        _clone(
            database,
            "scans",
            "legacy-scan-001",
            id="sonarr-scan",
            media_item_id="sonarr-episode",
            scan_number=1,
        )
        database.execute("UPDATE arr_settings SET api_key = 'arr-secret'")
        database.execute(
            "INSERT INTO arr_settings (service, server_url, api_key, updated_at) "
            "VALUES ('sonarr', 'http://sonarr.test', 'sonarr-secret', '2026-01-01')"
        )
    return path


def _expected(original, columns):
    """`original` with the work 0025 stops: scans cancel, queue rows go, jobs fail,
    and publications end cancelled."""
    expected = dict(original)
    status = columns["scans"].index("status")
    expected["scans"] = [
        row[:status] + ("cancelled",) + row[status + 1 :]
        if row[status] not in {"complete", "error", "failed", "cancelled"}
        else row
        for row in original["scans"]
    ]
    expected["scan_queue"] = []
    expected.setdefault("jellyfin_user_blocks", [])
    # The fixture's one pending publication is of the now archived scan.
    for table, column, value in (
        ("community_operations", "state", "failed"),
        ("community_publications", "cancel_requested", 1),
    ):
        index = columns[table].index(column)
        expected[table] = [
            row[:index] + (value,) + row[index + 1 :] if row[0] == "op-publish" else row
            for row in original[table]
        ]
    if "media_item_export_regenerations" in original:
        names = columns["media_item_export_regenerations"]
        state, due, reason = (names.index(name) for name in ("state", "due_at", "failure_reason"))
        jobs = []
        for row in original["media_item_export_regenerations"]:
            if row[state] in _UNFINISHED_JOBS:
                row = list(row)
                row[state], row[due], row[reason] = "failed", None, "regeneration_failed"
                row = tuple(row)
            jobs.append(row)
        expected["media_item_export_regenerations"] = jobs
    return expected


def _counts(rows):
    return {state.RENAMED.get(name, name): len(values) for name, values in rows.items()}


@pytest.mark.parametrize("revision", SOURCES)
def test_every_supported_revision_archives_arr_records_and_keeps_every_row(tmp_path, revision):
    path = _with_arr_records(tmp_path, revision)
    original, _ = _snapshot(path)
    assert original["scan_queue"]

    generation = adoption.upgrade_existing_database(path, path.parent)

    assert generation.is_dir()
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    upgraded, _ = _snapshot(path)
    drop_0031_additions(upgraded, original)
    after = _counts(upgraded)
    assert {name: after.get(name) for name in _counts(original)} == _counts(original) | {
        "scan_queue": 0
    }
    with sqlite3.connect(path) as database:
        assert sorted(database.execute("SELECT server_id, archived_source FROM media_items")) == [
            (_RADARR, "radarr"),
            (_SONARR, "sonarr"),
        ]
        assert database.execute(
            "SELECT id, status FROM scans WHERE status != 'complete' ORDER BY id"
        ).fetchall() == [("queued-scan-002", "cancelled")]
        assert database.execute(
            "SELECT service, server_url FROM arr_settings ORDER BY service"
        ).fetchall() == [("radarr", "https://example.invalid"), ("sonarr", "http://sonarr.test")]
        columns = {row[1] for row in database.execute("PRAGMA table_info(arr_settings)")}
        assert "api_key" not in columns
    assert adoption.upgrade_existing_database(path, path.parent) is None
    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert _snapshot(recovered)[0] == original


def test_the_previous_revision_changes_only_archived_work(tmp_path):
    path = _with_arr_records(tmp_path, SOURCES[-1])
    original, columns = _snapshot(path)
    adoption.upgrade_existing_database(path, path.parent)
    upgraded, _ = _snapshot(path)
    drop_0031_additions(upgraded, original)
    assert without_stable_media_identity(upgraded) == _expected(original, columns)


def test_access_grants_for_removed_sources_move_to_the_archive(tmp_path):
    path = _with_arr_records(tmp_path, SOURCES[-1])
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT INTO media_access_exceptions VALUES (1, 'sonarr', ?, 'episode:77', ?)",
            (_SONARR, "2026-01-01"),
        )
        database.execute(
            "INSERT INTO media_access_exceptions VALUES (1, 'manual_files', 'editarr-manual', "
            "'/media/kept.mkv', '2026-01-01')"
        )
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        assert database.execute(
            "SELECT source_table, row_json FROM archived_media_access_rows ORDER BY id"
        ).fetchall() == [
            (
                "media_access_scope_sources",
                '{"scope_id": 1, "source_id": "", "source_kind": "radarr"}',
            ),
            (
                "media_access_exceptions",
                '{"granted_at": "2026-01-01", "item_key": "episode:77", "server_key": '
                f'"{_SONARR}", "source_kind": "sonarr", "user_id": 1}}',
            ),
            (
                "media_access_exceptions",
                '{"granted_at": "2026-01-01", "item_key": "/media/kept.mkv", '
                '"server_key": "editarr-manual", "source_kind": "manual_files", "user_id": 1}',
            ),
        ]
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("INSERT INTO media_access_scope_sources VALUES (1, 'radarr', '')")
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("UPDATE media_items SET archived_source = 'other'")


def _clone_publication(database, operation_id, source_kind, source_id, scan_id):
    """Copy the fixture's pending publication under a new operation ID and source."""
    for table, key in (("community_operations", "scan_id"), ("community_publications", None)):
        columns = [row[1] for row in database.execute(f"PRAGMA table_info({table})")]
        changes = {"operation_id": operation_id}
        if key:
            changes[key] = scan_id
        else:
            changes |= {"source_kind": source_kind, "source_id": source_id}
        selected = ", ".join("?" if name in changes else name for name in columns)
        database.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) SELECT {selected} FROM {table} "
            "WHERE operation_id = 'op-publish'",
            [changes[name] for name in columns if name in changes],
        )


def test_unsent_publications_of_archived_work_end_cancelled_and_sent_ones_await_the_server(
    tmp_path,
):
    path = _with_arr_records(tmp_path, SOURCES[-1])
    with sqlite3.connect(path) as database:
        _clone(
            database,
            "media_items",
            "sonarr-episode",
            id="live-item",
            server_id="jellyfin-server",
            jellyfin_id="live",
        )
        _clone(database, "scans", "sonarr-scan", id="live-scan", media_item_id="live-item")
        (review,) = database.execute("SELECT id FROM saved_reviews LIMIT 1").fetchone()
        _clone_publication(database, "op-review", "review", review, None)
        _clone_publication(database, "op-sent", "scan", "sonarr-scan", "sonarr-scan")
        _clone_publication(database, "op-live", "scan", "live-scan", "live-scan")
        database.execute(
            "UPDATE community_operations SET state = 'submitted' WHERE operation_id = 'op-sent'"
        )
    adoption.upgrade_existing_database(path, path.parent)
    with sqlite3.connect(path) as database:
        assert database.execute(
            "SELECT operation.operation_id, operation.state, publication.cancel_requested "
            "FROM community_operations AS operation JOIN community_publications AS publication "
            "USING (operation_id) ORDER BY operation_id"
        ).fetchall() == [
            ("op-live", "pending", 0),
            ("op-publish", "failed", 1),
            ("op-review", "failed", 1),
            ("op-sent", "submitted", 1),
        ]
        assert database.execute(
            "SELECT operation_id, state FROM community_operations WHERE kind != 'publish' "
            "ORDER BY operation_id"
        ).fetchall() == [("op-receipt", "acknowledged"), ("op-vote", "submitted")]
