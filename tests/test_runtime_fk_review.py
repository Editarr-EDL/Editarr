"""Regression versions of the B2 review probes, retaining their names."""

import sqlite3

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

import scanner.db as runtime_db
from scanner.db import ReviewFilterRow, ReviewScanMembershipRow, ScanFindingRow, ScanRow
from scanner.persistence.migrations import preflight
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.repository import delete_scan
from scanner.settings.config import Settings
from tests.multi_user_access_support import OWNER
from tests.test_scan_access_http import app as app

OPERATION = (
    "INSERT INTO community_operations (operation_id, service_url, kind, state, scan_id, "
    "updated_at) VALUES (:id, 'https://community.example', 'publish', 'pending', :scan, "
    "'2026-10-24T00:00:00Z')"
)


def test_harness_engine_enforces_fks(app):
    with app.accounts.factory() as database:
        assert database.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


@pytest.mark.parametrize("kind", ["publish", "receipt", "vote"])
def test_references_precheck_says_deletable_but_delete_refuses(app, kind):
    scan_id, _ = app.seed_scan(OWNER, "retained", status="complete")
    with app.accounts.factory() as database:
        database.execute(
            text(OPERATION.replace("'publish'", f"'{kind}'")), {"id": "op-1", "scan": scan_id}
        )
        database.commit()
    precheck = app.send("GET", f"/api/scans/{scan_id}/references", app.accounts.cookie(OWNER))
    response = app.send("DELETE", f"/api/scans/{scan_id}", app.accounts.unsafe_cookie(OWNER))
    assert precheck.status_code == 200
    assert precheck.json()["community_in_progress"] is True
    assert precheck.json()["reviews"] == []
    assert precheck.json()["active_exports"] == []
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "scan_referenced"
    assert response.json()["detail"]["references"]["community_in_progress"] is True
    for private in ("community.example", "op-1"):
        assert private not in precheck.text
        assert private not in response.text


@pytest.mark.parametrize("kind", ["publish", "receipt", "vote", "review"])
@pytest.mark.parametrize("boundary", ["first_read", "delete_children"])
def test_evidence_committed_between_check_and_delete(app, kind, boundary):
    """A second writer cannot commit evidence while deletion checks its references."""
    scan_id, _ = app.seed_scan(OWNER, "racing", status="complete")
    attempts = []
    engine = app.accounts.factory.kw["bind"]
    statement_to_write = OPERATION.replace("'publish'", f"'{kind}'")
    values = {"id": "op-race", "scan": scan_id}
    if kind == "review":
        review_id = app.seed_review(OWNER, "racing-review", scan_id)
        with app.accounts.factory() as database:
            database.query(ReviewScanMembershipRow).filter_by(review_id=review_id).delete()
            database.commit()
        statement_to_write = (
            "INSERT INTO review_scan_memberships (review_id, scan_id, role, position) "
            "VALUES (:id, :scan, 'both', 0)"
        )
        values["id"] = review_id

    def inject(connection, cursor, statement, parameters, context, executemany):
        selected = (
            statement.lstrip().upper().startswith("SELECT")
            if boundary == "first_read"
            else statement.lstrip().upper().startswith("DELETE FROM SCAN_QUEUE")
        )
        if attempts or not selected:
            return
        attempts.append("attempted")
        with sqlite3.connect(engine.url.database, timeout=0) as writer:
            runtime_db.initialize_sqlite_connection(writer, None)
            try:
                writer.execute(statement_to_write, values)
                writer.commit()
            except sqlite3.OperationalError as error:
                assert "locked" in str(error)
                attempts.append("blocked")

    event.listen(engine, "before_cursor_execute", inject)
    try:
        with app.accounts.factory() as database:
            assert delete_scan(database, scan_id)
    finally:
        event.remove(engine, "before_cursor_execute", inject)
    assert attempts == ["attempted", "blocked"]
    with app.accounts.factory() as database:
        assert database.get(ScanRow, scan_id) is None
        assert database.execute(text("PRAGMA foreign_key_check")).all() == []


def test_runtime_pragma_applies_on_first_use_inside_begin(monkeypatch, tmp_path):
    migrate_fresh_database(tmp_path / "scanner.db", tmp_path)
    monkeypatch.setattr(runtime_db, "settings", Settings(data_dir=tmp_path))
    engine = runtime_db.get_engine()
    try:
        with engine.begin() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            with pytest.raises(IntegrityError):
                connection.execute(text(OPERATION), {"id": "invalid", "scan": "missing"})
    finally:
        engine.dispose()


def test_filter_only_reference_blocks_deletion_without_a_membership(app):
    scan_id, _ = app.seed_scan(OWNER, "copied-reference", status="complete")
    review_id = app.seed_review(OWNER, "copied-review", scan_id)
    with app.accounts.factory() as database:
        finding = ScanFindingRow(
            scan_id=scan_id,
            source="audio",
            start_seconds=1,
            end_seconds=2,
            selected=True,
            edit_action="mute",
        )
        database.add(finding)
        database.flush()
        database.add(
            ReviewFilterRow(
                id="copied-filter",
                review_id=review_id,
                source_scan_id=scan_id,
                source_finding_id=finding.id,
                source="audio",
                start_seconds=1,
                end_seconds=2,
                selected=True,
                edit_action="mute",
                position=0,
            )
        )
        database.query(ReviewScanMembershipRow).filter_by(review_id=review_id).delete()
        database.commit()
    precheck = app.send("GET", f"/api/scans/{scan_id}/references", app.accounts.cookie(OWNER))
    assert precheck.status_code == 200, precheck.text
    assert [value["id"] for value in precheck.json()["reviews"]] == [review_id]
    response = app.send("DELETE", f"/api/scans/{scan_id}", app.accounts.unsafe_cookie(OWNER))
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "scan_referenced"


def test_orphaned_existing_database_startup_message(monkeypatch, tmp_path):
    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    with sqlite3.connect(path) as database:
        database.execute(OPERATION, {"id": "private-operation", "scan": "private-scan"})
        database.execute(OPERATION, {"id": "private-operation-2", "scan": "private-scan"})
    configuration = Settings(data_dir=tmp_path)
    monkeypatch.setattr(runtime_db, "settings", configuration)
    monkeypatch.setattr(preflight, "settings", configuration)
    with pytest.raises(ValueError) as caught:
        runtime_db.init_db()
    message = str(caught.value)
    assert "community_operations" in message
    assert "scans" in message
    assert "count=2" in message
    assert "private-operation" not in message
    assert "private-scan" not in message
