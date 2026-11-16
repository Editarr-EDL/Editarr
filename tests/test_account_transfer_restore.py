"""A restored backup never makes transfer or link recovery remove the only usable file.

A backup can capture move rows mid-transfer, so restored rows disagree with the disk.
Recovery keeps its round-2 decisions, but removes a file only under the shared removal
rule (`export_identity_moves.removable`) and never writes export bytes. The restore
report lists each disagreement and blocks nothing (ruling wave14-fresh-install-restore);
every move closes and every usable copy stays. Review round 3 (R3-F1), through real
`export_backup`, `preview_restore`, and `activate_restore` into an empty installation,
and real child-process exits.
"""

import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scanner.backup.archive import export_backup
from scanner.db import MediaItemExportRow
from scanner.persistence.repository import account_transfer_files, export_committed
from scanner.persistence.rows.db_account_dispositions import (
    AccountDispositionRow,
    AccountTransferMoveRow,
)
from scanner.security.auth_repository import issue_session
from tests.multi_user_access_support import ADMIN, OWNER, USER
from tests.test_account_deletion_http import delete, preview
from tests.test_account_transfer_http import app as app
from tests.test_account_transfer_http import exports_app, review_app
from tests.test_account_transfer_recovery import (
    TRANSFER_AND_EXIT,
    _crash,
    _new_paths,
    _published,
)
from tests.test_backup_activation import _fixture
from tests.test_backup_activation import _restore as _restore_into
from tests.test_export_regeneration_ownership import sweep

__all__ = ["exports_app", "review_app"]

SNAPSHOT_AFTER_WRITE = """from scanner.backup.archive import export_backup
from pathlib import Path
original_write = account_transfer_files.write
def captured_write(*args, **kwargs):
    original_write(*args, **kwargs)
    source = Path(engine.url.database)
    export_backup(source, source.parent, Path(sys.argv[6]))
account_transfer_files.write = captured_write
"""


def _target(tmp_path):
    fixture = tmp_path / "portable"
    fixture.mkdir()
    return _fixture(fixture)[1]


def _restore(archive, database: Path):
    """Restore into the empty installation beside `database`; returns the report findings."""
    return {
        (item["export_id"], item["finding"], item["path"])
        for item in _restore_into(archive, database.parent)
    }


@contextmanager
def _restored(app, monkeypatch, database: Path):
    """Serve `app` from the restored `database` with a fresh admin session."""
    engine = create_engine("sqlite:///" + str(database))
    factory = sessionmaker(bind=engine)
    try:
        with factory() as db:
            app.accounts.sessions[ADMIN] = issue_session(
                db, remember_me=False, user_id=ADMIN, configuration=app.accounts.configuration
            )
        with monkeypatch.context() as patch:
            patch.setattr(app.accounts, "factory", factory)
            yield factory
    finally:
        engine.dispose()


def _restored_family(database: Path):
    """The restored transfer state, read fresh: the disposition modes and the moves."""
    with sqlite3.connect(database) as connection:
        return (
            connection.execute("SELECT user_id, mode FROM account_dispositions").fetchall(),
            sorted(connection.execute("SELECT export_id FROM account_transfer_moves")),
        )


def _recover(app, trigger):
    if trigger == "sweep":
        sweep(app)
        sweep(app)
        return
    response = preview(app, ADMIN, USER, "retain")
    assert response.status_code == 200, response.text


def _served(factory, ids, content):
    """Every publication serves its original bytes from the path it records."""
    with factory() as db:
        for key in ids:
            path = Path(db.get(MediaItemExportRow, key).destination_path)
            assert path.read_bytes() == content[key], "a restored publication lost its file"


def owner_writes_committed(factory, ids) -> None:
    """The owner's remedy, `editarr admin write-export`, for each publication."""
    with factory() as db:
        for key in ids:
            export_committed.write_committed(db, key, OWNER)


def _open_moves(factory) -> int:
    with factory() as db:
        return db.query(AccountTransferMoveRow).filter_by(outcome=None).count()


def _snapshot_live_transfer(app, monkeypatch, archive):
    """Back up after the transfer wrote its new files, then let it finish."""
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    original = account_transfer_files.write

    def write_and_snapshot(*args, **kwargs):
        original(*args, **kwargs)
        export_backup(app.accounts.path, app.accounts.path.parent, archive)

    with monkeypatch.context() as patch:
        patch.setattr(account_transfer_files, "write", write_and_snapshot)
        response = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=ADMIN)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("trigger", ["sweep", "preview"])
def test_a_backup_taken_during_a_transfer_restores_without_losing_its_files(
    app, tmp_path, monkeypatch, trigger
):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    target = _target(tmp_path)
    archive = tmp_path / "midtransfer.backup"
    _snapshot_live_transfer(app, monkeypatch, archive)
    assert all(not before[key]["path"].exists() and new[key].exists() for key in ids)
    report = _restore(archive, target / "scanner.db")
    assert report == {
        *((key, "recorded_path_missing", str(before[key]["path"])) for key in ids),
        *((key, "unrecorded_copy", str(new[key])) for key in ids),
    }
    with _restored(app, monkeypatch, target / "scanner.db") as factory:
        _recover(app, trigger)
        assert all(new[key].read_bytes() == content[key] for key in ids)
        assert _open_moves(factory) == 0
        owner_writes_committed(factory, ids)
        _served(factory, ids, content)


@pytest.mark.parametrize("stage", ["written", "switched"])
def test_a_backup_of_an_interrupted_transfer_restores_to_served_files(
    app, tmp_path, monkeypatch, stage
):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    _crash(app, stage, body["confirmation"])
    target = _target(tmp_path)
    archive = tmp_path / "interrupted.backup"
    export_backup(app.accounts.path, app.accounts.path.parent, archive)
    if stage == "written":
        response = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=ADMIN)
        assert response.status_code == 200, response.text
    else:
        sweep(app)
    assert all(not before[key]["path"].exists() and new[key].exists() for key in ids)
    _restore(archive, target / "scanner.db")
    dispositions, moves = _restored_family(target / "scanner.db")
    assert dispositions == ([(USER, "transfer")] if stage == "switched" else [])
    assert moves == [(key,) for key in sorted(ids)]
    with _restored(app, monkeypatch, target / "scanner.db") as factory:
        sweep(app)
        sweep(app)
        assert _open_moves(factory) == 0
        with factory() as db:
            assert (db.get(AccountDispositionRow, USER) is not None) == (stage == "switched")
        if stage == "written":
            assert all(new[key].read_bytes() == content[key] for key in ids)
        else:
            _served(factory, ids, content)


def test_a_restore_over_pending_cleanup_keeps_every_recorded_file(app, tmp_path, monkeypatch):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    archive = tmp_path / "pending-cleanup.backup"
    script = TRANSFER_AND_EXIT.replace(
        "with Session(engine) as db:", SNAPSHOT_AFTER_WRITE + "with Session(engine) as db:"
    )
    arguments = [str(app.accounts.engine.url), str(USER), str(ADMIN), "switched"]
    arguments += [body["confirmation"], str(archive)]
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, *arguments], capture_output=True, timeout=60
    )
    assert result.returncode == 75, result.stderr.decode()
    assert all(before[key]["path"].exists() and new[key].exists() for key in ids)
    app.accounts.engine.dispose()
    target = _target(tmp_path)
    _restore(archive, target / "scanner.db")
    # The backup ran before the switch, so no disposition restores.
    assert _restored_family(target / "scanner.db") == ([], [(key,) for key in sorted(ids)])
    with _restored(app, monkeypatch, target / "scanner.db") as factory:
        response = preview(app, ADMIN, USER, "retain")
        assert response.status_code == 200, response.text
        assert response.json()["blockers"] == []
        _served(factory, ids, content)
        assert _open_moves(factory) == 0


def test_a_restored_move_whose_publication_cannot_be_served_keeps_every_file(
    app, tmp_path, monkeypatch
):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    target = _target(tmp_path)
    archive = tmp_path / "midtransfer.backup"
    _snapshot_live_transfer(app, monkeypatch, archive)
    _restore(archive, target / "scanner.db")
    for key in ids:
        before[key]["path"].parent.mkdir(parents=True, exist_ok=True)
        before[key]["path"].write_bytes(b"not the published bytes")
    with _restored(app, monkeypatch, target / "scanner.db") as factory:
        sweep(app)
        assert _open_moves(factory) == 0
        assert all(new[key].read_bytes() == content[key] for key in ids)
        assert all(before[key]["path"].read_bytes() == b"not the published bytes" for key in ids)


def test_a_restored_move_never_recreates_a_missing_media_directory(app, tmp_path, monkeypatch):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    target = _target(tmp_path)
    archive = tmp_path / "midtransfer.backup"
    _snapshot_live_transfer(app, monkeypatch, archive)
    _restore(archive, target / "scanner.db")
    media_directory = before[ids[0]]["path"].parents[2]
    unmounted = media_directory.with_name(media_directory.name + ".unmounted")
    media_directory.rename(unmounted)
    with _restored(app, monkeypatch, target / "scanner.db") as factory:
        sweep(app)
        assert not media_directory.exists()
        assert _open_moves(factory) == 0
    unmounted.rename(media_directory)
    assert all(new[key].read_bytes() == content[key] for key in ids)
