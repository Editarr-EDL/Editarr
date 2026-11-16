import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from scanner.db import AuditEventRow, MediaItemExportRow
from tests.multi_user_access_support import ADMIN, USER
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("target", ["self", "item_default"])
def test_windows_publication_and_deletion(app, monkeypatch, format, target):
    from scanner.persistence.repository import export_recovery
    from scanner.runtime import durability

    review = seeded_review(app, USER, "windows-directory")
    real_open = os.open

    def windows_open(path, flags, *args, **kwargs):
        if not isinstance(path, int) and Path(path).is_dir():
            raise PermissionError(errno.EACCES, "Windows CRT directory open", str(path))
        return real_open(path, flags, *args, **kwargs)

    values = {name: getattr(os, name) for name in dir(os)}
    values.update(name="nt", open=windows_open)
    windows_os = SimpleNamespace(**values)
    monkeypatch.setattr(export_recovery, "os", windows_os)
    monkeypatch.setattr(durability, "os", windows_os)
    actor = ADMIN if target == "item_default" else USER
    response = publish(app, review, format, user=actor, target=target)
    assert response.status_code == 200, response.text
    status = response.json()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, status["id"])
        destination = Path(row.destination_path)
        assert destination.read_bytes() == row.content_bytes
        before = row.content_bytes
        db.execute(
            text(
                "CREATE TRIGGER fail_publication BEFORE INSERT ON audit_events "
                "WHEN NEW.action = 'export.publish' BEGIN SELECT RAISE(ABORT, 'fault'); END"
            )
        )
        db.commit()
    failed = publish(app, review, format, user=actor, target=target)
    assert failed.status_code == 500, failed.text
    assert destination.read_bytes() == before
    with app.accounts.factory() as db:
        assert db.get(MediaItemExportRow, status["id"]).publication_revision == 1
    deleted = app.send(
        "DELETE",
        f"/api/exports/{status['id']}?revision={status['publication_revision']}"
        f"&publication_id={status['publication_id']}",
        app.accounts.unsafe_cookie(actor),
    )
    assert deleted.status_code == 204, deleted.text
    assert not destination.exists()
    with app.accounts.factory() as db:
        assert db.get(MediaItemExportRow, status["id"]) is None
        assert db.query(AuditEventRow).filter_by(action="export.delete").count() == 1


@pytest.mark.parametrize("operation", ["posix_directory", "windows_file"])
def test_supported_fsync_errors_are_not_suppressed(app, monkeypatch, operation):
    import stat

    from scanner.persistence.repository import export_recovery
    from scanner.runtime import durability

    review = seeded_review(app, USER, "disk-failure")
    real_fsync = os.fsync

    def failing_fsync(descriptor):
        directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if directory == (operation == "posix_directory"):
            raise OSError(errno.EIO, "injected disk failure")
        return real_fsync(descriptor)

    values = {name: getattr(os, name) for name in dir(os)}
    values.update(name="posix" if operation == "posix_directory" else "nt", fsync=failing_fsync)
    boundary = SimpleNamespace(**values)
    monkeypatch.setattr(export_recovery, "os", boundary)
    monkeypatch.setattr(durability, "os", boundary)
    response = publish(app, review, "edl")
    assert response.status_code == 500, response.text
    with app.accounts.factory() as db:
        assert db.query(MediaItemExportRow).count() == 0
        assert db.query(AuditEventRow).filter_by(action="export.publish").count() == 0


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("target", ["self", "item_default"])
def test_new_export_directory_entries_are_durable(app, monkeypatch, format, target):
    from scanner.persistence.repository import export_recovery

    synced = []
    real_sync = export_recovery._sync

    def record(directory):
        synced.append(Path(directory).resolve())
        return real_sync(directory)

    monkeypatch.setattr(export_recovery, "_sync", record)
    review = seeded_review(app, USER, "fresh-directories")
    response = publish(
        app, review, format, user=ADMIN if target == "item_default" else USER, target=target
    )
    assert response.status_code == 200, response.text
    with app.accounts.factory() as db:
        destination = Path(db.get(MediaItemExportRow, response.json()["id"]).destination_path)
    required = [destination.parent, destination.parent.parent]
    if target == "self":
        required.append(destination.parent.parent.parent)
    assert all(directory.resolve() in synced for directory in required)


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("target", ["self", "item_default"])
def test_retry_syncs_parent_left_unsynced_by_prior_failure(app, monkeypatch, format, target):
    from scanner.persistence.repository import export_recovery

    review = seeded_review(app, USER, "directory-retry")
    root = app.media.resolve()
    original = export_recovery._sync
    failed = False
    synced = []

    def failure_once(directory):
        nonlocal failed
        directory = Path(directory).resolve()
        if directory == root and not failed:
            failed = True
            raise OSError(errno.EIO, "injected parent directory sync failure")
        original(directory)
        synced.append(directory)

    monkeypatch.setattr(export_recovery, "_sync", failure_once)
    actor = ADMIN if target == "item_default" else USER
    first = publish(app, review, format, user=actor, target=target)
    assert failed
    assert first.status_code == 500, first.text
    assert (root / format).is_dir()
    retry = publish(app, review, format, user=actor, target=target)
    assert retry.status_code == 200, retry.text
    assert root in synced, "Successful retry never persisted the format directory entry"
