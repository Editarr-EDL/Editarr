"""Runtime deletion must preserve durable community evidence and FK integrity."""

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import scanner.db as runtime_db
from scanner.backup.archive import export_backup
from scanner.backup.snapshots import create_safety_snapshot, validate_safety_snapshot
from scanner.db import CommunityImportRow, CommunityImportTargetRow, CommunityOperationRow, ScanRow
from scanner.persistence.migrations import preflight
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.persistence.repository.community_import_projection import LocalImportTarget
from scanner.persistence.repository.community_imports import import_verified_download
from scanner.persistence.repository.repository_community import set_connection
from scanner.repository import ScanReferencedError, delete_scan, get_scan_references
from scanner.security.community_identity import (
    load_or_create_installation_identity,
    save_identity_registration,
)
from scanner.settings.config import Settings
from tests import test_community_verified_import as community_fixtures
from tests.community_identity_support import identity_home, only_identity_ref
from tests.multi_user_access_support import OWNER
from tests.retired_protocol_support import RETIRED_REVISION_PROTOCOL
from tests.runtime_fk_inventory import EXPECTED_FOREIGN_KEYS
from tests.test_community_verified_import import (
    BOUNDS,
    SERVICE,
    local_target,
    verified_input,
)
from tests.test_scan_access_http import app as app

community_import_database = community_fixtures.import_database


@pytest.fixture
def import_database(community_import_database):
    community_import_database.dispose()
    event.listen(community_import_database, "connect", runtime_db.initialize_sqlite_connection)
    return community_import_database


@pytest.mark.parametrize(
    "operation_state",
    [
        None,
        *(
            pytest.param(state, marks=RETIRED_REVISION_PROTOCOL)
            for state in ("pending", "submitted", "acknowledged", "failed")
        ),
    ],
)
def test_imported_scan_deletion_waits_only_for_active_operations(
    import_database, tmp_path, operation_state
):
    downloaded = verified_input()
    with Session(import_database) as database:
        assert database.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        imported = import_verified_download(
            database,
            downloaded,
            local_target(downloaded, tmp_path),
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(database),
        )
        scan_id = imported.scan.id
        if operation_state is None:
            database.query(CommunityOperationRow).delete()
        else:
            database.query(CommunityOperationRow).update({"state": operation_state})
        database.commit()
        before = database.get(CommunityImportRow, imported.import_id).artifact_bytes
        blocked = operation_state in {"pending", "submitted"}
        assert get_scan_references(database, scan_id).community_in_progress == blocked
        if blocked:
            with pytest.raises(ScanReferencedError, match="still in progress"):
                delete_scan(database, scan_id)
        else:
            assert delete_scan(database, scan_id)

    with Session(import_database) as database:
        assert (database.get(ScanRow, scan_id) is not None) == blocked
        imported_row = database.get(CommunityImportRow, imported.import_id)
        assert (imported_row is not None) == blocked
        if blocked:
            assert imported_row.artifact_bytes == before
        assert database.query(CommunityImportTargetRow).count() == int(blocked)
        assert database.query(CommunityOperationRow).count() == int(operation_state is not None)
        if operation_state is not None:
            operation = database.query(CommunityOperationRow).one()
            assert operation.scan_id == (scan_id if blocked else None)
            assert operation.state == operation_state
        assert database.execute(text("PRAGMA foreign_key_check")).all() == []


@pytest.mark.parametrize("state", ["pending", "submitted", "acknowledged", "failed"])
@RETIRED_REVISION_PROTOCOL
def test_imported_scan_http_deletion_matches_precheck(app, tmp_path, monkeypatch, state):
    original_id, _ = app.seed_scan(OWNER, "import-target", status="complete")
    downloaded = verified_input()
    target_evidence = local_target(downloaded, tmp_path).evidence
    home = identity_home(app.accounts.configuration.data_dir)
    load_or_create_installation_identity(home)
    identity = save_identity_registration(
        home,
        contributor_id="reader",
        service_url=SERVICE,
        last_known_key_state="active",
    )
    with app.accounts.factory() as database:
        original = database.get(ScanRow, original_id)
        target = LocalImportTarget(
            original.media_item_id, None, original.resolved_local_path, target_evidence
        )
        credential_ref = only_identity_ref(database)
        set_connection(
            database,
            credential_ref=credential_ref,
            service_url=SERVICE,
            contributor_id="reader",
            installation_id=identity.installation_id,
            key_id=identity.key_id,
        )
        database.commit()
        imported = import_verified_download(
            database,
            downloaded,
            target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=credential_ref,
        )
        database.query(CommunityOperationRow).update({"state": state})
        database.commit()

    precheck = app.send(
        "GET", f"/api/scans/{imported.scan.id}/references", app.accounts.cookie(OWNER)
    )
    response = app.send(
        "DELETE", f"/api/scans/{imported.scan.id}", app.accounts.unsafe_cookie(OWNER)
    )

    blocked = state in {"pending", "submitted"}
    assert precheck.json()["community_in_progress"] == blocked
    assert response.status_code == (409 if blocked else 200), response.text
    if blocked:
        assert response.json()["detail"]["code"] == "scan_referenced"
        assert "still in progress" in response.json()["detail"]["message"]
    assert not any(
        private in response.text
        for private in (SERVICE, "private-installation", "private-key", str(tmp_path))
    )
    with app.accounts.factory() as database:
        assert (database.get(ScanRow, imported.scan.id) is not None) == blocked
        assert (database.get(CommunityImportRow, imported.import_id) is not None) == blocked
        assert database.query(CommunityImportTargetRow).count() == int(blocked)
        assert database.query(CommunityOperationRow).count() == 1
        assert database.query(CommunityOperationRow).one().scan_id == (
            imported.scan.id if blocked else None
        )
        assert database.execute(text("PRAGMA foreign_key_check")).all() == []
    monkeypatch.setattr(preflight, "settings", app.accounts.configuration)
    assert preflight.prepare_database() == preflight.HEAD_REVISION
    assert preflight.require_supported_schema() == preflight.HEAD_REVISION
    snapshot = create_safety_snapshot(app.accounts.path, app.accounts.configuration.data_dir)
    assert validate_safety_snapshot(snapshot)["format_version"] == 1
    destination = tmp_path / "exports" / "after-refused-delete.backup"
    export_backup(app.accounts.path, app.accounts.configuration.data_dir, destination)
    assert destination.stat().st_size > 0


@pytest.mark.parametrize("kind", ["publish", "submission", "filter_vote"])
@pytest.mark.parametrize("state", ["pending", "submitted", "acknowledged", "failed"])
def test_only_in_progress_operations_block_scan_deletion(app, kind, state):
    scan_id, _ = app.seed_scan(OWNER, "operation-target", status="complete")
    with app.accounts.factory() as database:
        database.add(
            CommunityOperationRow(
                operation_id="retained-operation",
                service_url=SERVICE,
                kind=kind,
                state=state,
                scan_id=scan_id,
                updated_at="2026-10-24T00:00:00Z",
            )
        )
        database.commit()
        blocked = state in {"pending", "submitted"}
        assert get_scan_references(database, scan_id).community_in_progress == blocked
        if blocked:
            with pytest.raises(ScanReferencedError, match="still in progress"):
                delete_scan(database, scan_id)
        else:
            assert delete_scan(database, scan_id)
        assert (database.get(ScanRow, scan_id) is not None) == blocked
        assert database.get(CommunityOperationRow, "retained-operation").state == state
        assert database.execute(text("PRAGMA foreign_key_check")).all() == []


def test_runtime_connections_enforce_foreign_keys_including_after_reconnect(monkeypatch, tmp_path):
    configuration = Settings(data_dir=tmp_path)
    migrate_fresh_database(tmp_path / "scanner.db", tmp_path)
    monkeypatch.setattr(runtime_db, "settings", configuration)
    engine = runtime_db.get_engine()
    try:
        for _attempt in range(2):
            with engine.connect() as first, engine.connect() as second:
                for connection in (first, second):
                    assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            engine.dispose()
    finally:
        engine.dispose()


def test_every_head_foreign_key_has_a_reviewed_deletion_policy(tmp_path):
    from sqlalchemy import create_engine

    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        inspector = inspect(engine)
        actual = {
            "|".join(
                (
                    table,
                    ",".join(foreign_key["constrained_columns"]),
                    foreign_key["referred_table"],
                    ",".join(foreign_key["referred_columns"]),
                    foreign_key["options"].get("ondelete", "NO ACTION"),
                )
            )
            for table in inspector.get_table_names()
            for foreign_key in inspector.get_foreign_keys(table)
        }
        assert actual == EXPECTED_FOREIGN_KEYS
        assert {
            "|".join(
                (
                    table.name,
                    ",".join(element.parent.name for element in constraint.elements),
                    constraint.referred_table.name,
                    ",".join(element.column.name for element in constraint.elements),
                    constraint.ondelete or "NO ACTION",
                )
            )
            for table in runtime_db.Base.metadata.tables.values()
            for constraint in table.foreign_key_constraints
        } == EXPECTED_FOREIGN_KEYS
    finally:
        engine.dispose()


@pytest.mark.parametrize("action", ["CASCADE", "RESTRICT", "NO ACTION", "SET NULL"])
def test_runtime_enforces_declared_delete_actions(monkeypatch, tmp_path, action):
    configuration = Settings(data_dir=tmp_path)
    migrate_fresh_database(tmp_path / "scanner.db", tmp_path)
    monkeypatch.setattr(runtime_db, "settings", configuration)
    engine = runtime_db.get_engine()
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE specimen_parent (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE specimen_child (id INTEGER PRIMARY KEY, parent_id INTEGER "
                f"REFERENCES specimen_parent(id) ON DELETE {action})"
            )
            connection.exec_driver_sql("INSERT INTO specimen_parent VALUES (1)")
            connection.exec_driver_sql("INSERT INTO specimen_child VALUES (1, 1)")
        if action in ("RESTRICT", "NO ACTION"):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.exec_driver_sql("DELETE FROM specimen_parent")
        else:
            with engine.begin() as connection:
                connection.exec_driver_sql("DELETE FROM specimen_parent")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert connection.exec_driver_sql("SELECT parent_id FROM specimen_child").all() == (
                [] if action == "CASCADE" else [(None,)] if action == "SET NULL" else [(1,)]
            )
    finally:
        engine.dispose()
