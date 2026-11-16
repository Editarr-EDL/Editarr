# ruff: noqa: F811
"""`editarr admin write-export`: the owner writes a publication's committed bytes back.

It replaces the documented sqlite3 `writefile` remedy (ruling wave13-mu38-defer-restore).
It creates an empty recorded path, leaves the committed bytes where they already are,
and refuses anything else. It needs Jellyfin to accept the current owner's password,
records `export.write_committed` for a refused password as for a write, and runs
beside the app under the export publication lock. Filesystem races, interrupts, and
crashes are in `test_cli_write_export_crash_safety`.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from scanner import cli
from scanner.commands import admin
from scanner.db import (
    AuditEventRow,
    JellyfinAccountSyncRow,
    JellyfinSettingsRow,
    MediaItemExportRow,
    UserProviderIdentityRow,
    get_session_factory,
)
from scanner.persistence.repository.export_recovery import publication_lock
from scanner.runtime.maintenance import DatabaseMaintenance
from tests.cli_support import _run
from tests.cli_write_export_support import (
    _digest,
    _events,
    _published,
    _refused,
    _write,
    jellyfin,
    published,
)
from tests.multi_user_access_support import MEMBER_PASSWORD, OWNER, OWNER_PASSWORD
from tests.test_personal_exports_http import app, review_app

__all__ = ["app", "jellyfin", "published", "review_app"]


def test_an_empty_recorded_path_gets_the_committed_bytes(app, published, monkeypatch, capsys):
    export_id, path, content = published
    path.unlink()
    path.parent.rmdir()
    _write(monkeypatch, export_id)
    assert capsys.readouterr().out == f"Wrote export {export_id}'s committed bytes to {path}\n"
    assert _digest(path) == hashlib.sha256(content).hexdigest()
    assert _events(app) == [("succeeded", OWNER, None)]
    assert not list(path.parent.glob(".*"))


def test_the_committed_bytes_already_in_place_are_left_alone(app, published, monkeypatch, capsys):
    export_id, path, content = published
    before = os.stat(path)
    _write(monkeypatch, export_id)
    assert capsys.readouterr().out == (
        f"Export {export_id}'s committed bytes are already present at {path}\n"
    )
    assert path.read_bytes() == content and os.path.samestat(os.stat(path), before)
    assert _events(app) == [("succeeded", OWNER, None)]


def test_user_edited_bytes_are_never_overwritten(app, published, monkeypatch, capsys):
    export_id, path, content = published
    path.write_bytes(content + b"\n* user note\n")
    error = _refused(monkeypatch, capsys, export_id)
    assert "export_file_collision" in error
    assert path.read_bytes() == content + b"\n* user note\n"
    assert _events(app) == [("failed", OWNER, "export_file_collision")]


@pytest.mark.parametrize("entry", ["file", "id_directory", "loop"])
def test_a_symbolic_link_never_leads_the_write_out_of_the_layout(
    app, published, monkeypatch, capsys, tmp_path, entry
):
    export_id, path, _content = published
    outside = tmp_path / "outside"
    outside.mkdir()
    path.unlink()
    if entry == "file":
        path.symlink_to(outside / path.name)
    elif entry == "id_directory":
        path.parent.rmdir()
        path.parent.symlink_to(outside, target_is_directory=True)
    else:
        path.parent.rmdir()
        path.parent.symlink_to(path.parent)
    error = _refused(monkeypatch, capsys, export_id)
    assert "export_destination_changed" in error or "export_destination_unavailable" in error
    assert list(outside.iterdir()) == []
    assert [outcome for outcome, *_ in _events(app)] == ["failed"]


def test_a_missing_media_directory_is_never_created(app, published, monkeypatch, capsys):
    export_id, path, _content = published
    media_directory = path.parents[2]
    unmounted = media_directory.with_name(media_directory.name + ".unmounted")
    media_directory.rename(unmounted)
    try:
        error = _refused(monkeypatch, capsys, export_id)
        assert "export_destination_unavailable" in error
        assert not media_directory.exists()
    finally:
        unmounted.rename(media_directory)


def test_only_the_owners_password_writes(app, published, monkeypatch, capsys):
    export_id, path, _content = published
    path.unlink()
    error = _refused(monkeypatch, capsys, export_id, MEMBER_PASSWORD)
    assert "Jellyfin did not accept the owner's password" in error
    assert not path.exists()
    assert _events(app) == [("failed", OWNER, "reauthentication_failed")]


def test_an_owner_without_a_jellyfin_link_is_sent_to_snapshot_recovery(
    app, published, monkeypatch, capsys
):
    export_id, path, _content = published
    path.unlink()
    with app.accounts.factory() as db:
        db.query(UserProviderIdentityRow).filter_by(user_id=OWNER).delete()
        db.commit()
    error = _refused(monkeypatch, capsys, export_id, OWNER_PASSWORD)
    assert "no Jellyfin link" in error and "migration recover-export" in error
    assert not path.exists()
    assert _events(app) == []


def test_an_unreachable_jellyfin_writes_and_records_nothing(app, published, monkeypatch, capsys):
    export_id, path, _content = published
    path.unlink()
    with app.accounts.factory() as db:
        db.get(JellyfinSettingsRow, 1).server_url = "http://127.0.0.1:9"
        db.commit()
    assert "Jellyfin could not be reached" in _refused(monkeypatch, capsys, export_id)
    assert not path.exists()
    assert _events(app) == []


def test_a_link_without_a_stored_jellyfin_name_is_not_a_wrong_password(
    app, published, monkeypatch, capsys
):
    export_id, path, _content = published
    path.unlink()
    with app.accounts.factory() as db:
        db.query(JellyfinAccountSyncRow).filter_by(user_id=OWNER).delete()
        db.commit()
    error = _refused(monkeypatch, capsys, export_id, OWNER_PASSWORD)
    assert "Jellyfin name" in error and "did not accept" not in error
    assert not path.exists()
    assert _events(app) == [("failed", OWNER, "jellyfin_name_unknown")]


def test_unknown_and_withdrawn_publications_are_refused(app, published, monkeypatch, capsys):
    export_id, path, _content = published
    assert "No publication has ID 999" in _refused(monkeypatch, capsys, 999)
    with app.accounts.factory() as db:
        db.get(MediaItemExportRow, export_id).content_bytes = None
        db.commit()
    path.unlink()
    assert "export_withdrawn" in _refused(monkeypatch, capsys, export_id)
    assert not path.exists()
    assert _events(app) == [("failed", OWNER, "not_found"), ("failed", OWNER, "export_withdrawn")]


@pytest.mark.parametrize("password", ["", OWNER_PASSWORD, "wrong password"])
def test_a_refusal_for_a_missing_publication_records_only_its_id(
    app, published, monkeypatch, capsys, password
):
    _refused(monkeypatch, capsys, 999, password)
    with app.accounts.factory() as db:
        (row,) = db.query(AuditEventRow).filter_by(action="export.write_committed").all()
    reason = "not_found" if password == OWNER_PASSWORD else "reauthentication_failed"
    assert (row.resource, row.media_key, row.target_user_id) == ("export:999", None, None)
    assert json.loads(row.details_json) == {"reason": reason}


def test_an_empty_password_is_refused_and_audited(app, published, monkeypatch, capsys):
    export_id, path, _content = published
    path.unlink()
    assert "Jellyfin did not accept the owner's password" in _refused(
        monkeypatch, capsys, export_id, ""
    )
    assert not path.exists()
    assert _events(app) == [("failed", OWNER, "reauthentication_failed")]


@pytest.mark.parametrize("export_id", ["0", "-1", str(2**63), "one"])
def test_only_a_publication_id_is_accepted(published, monkeypatch, export_id):
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, ["admin", "write-export", export_id, "--password-stdin"])
    assert exit_info.value.code == 2


def test_the_longest_published_name_is_written(app, jellyfin, monkeypatch, capsys):
    """MU-31's journal name adds 23 bytes, so 232 is the longest name a publish can record."""
    stored = app.file
    monkeypatch.setattr(app, "file", lambda _name: stored("n" * 184 + ".mkv"))
    export_id, path, content = _published(app, monkeypatch, "long")
    assert len(path.name) == 232
    path.unlink()
    _write(monkeypatch, export_id)
    assert path.read_bytes() == content and not list(path.parent.glob(".*"))
    assert _events(app) == [("succeeded", OWNER, None)]


def test_it_runs_beside_the_app_and_waits_for_the_publication_lock(app, published, monkeypatch):
    export_id, path, content = published
    path.unlink()
    held, release, done = threading.Event(), threading.Event(), threading.Event()

    def publisher():
        with app.accounts.factory() as db, publication_lock(db):
            held.set()
            release.wait(10)

    holder = threading.Thread(target=publisher)
    holder.start()
    assert held.wait(10)
    writer = threading.Thread(target=lambda: (_write(monkeypatch, export_id), done.set()))
    writer.start()
    time.sleep(0.5)
    assert not done.is_set() and not path.exists()
    release.set()
    writer.join(20)
    holder.join(10)
    assert done.is_set() and path.read_bytes() == content


def test_it_refuses_during_database_maintenance(app, published, monkeypatch, capsys):
    """The production engine takes the maintenance lease on every connection."""
    export_id, path, _content = published
    data_dir = app.accounts.path.parent
    monkeypatch.setattr(cli.settings, "database_url", f"sqlite:///{app.accounts.path}")
    monkeypatch.setattr(admin, "get_session_factory", get_session_factory)
    path.unlink()
    held, release = threading.Event(), threading.Event()

    def maintain():
        with DatabaseMaintenance(data_dir).exclusive():
            held.set()
            release.wait(10)

    owner = threading.Thread(target=maintain)
    owner.start()
    try:
        assert held.wait(10)
        error = _refused(monkeypatch, capsys, export_id)
    finally:
        release.set()
        owner.join(10)
    assert "Database maintenance holds this data directory" in error
    assert not path.exists()
    _write(monkeypatch, export_id)
    assert path.exists()


def test_the_installed_command_writes_through_a_real_process(app, published, tmp_path):
    export_id, path, content = published
    path.unlink()
    environment = {
        **os.environ,
        "EDITARR_DATA_DIR": str(app.accounts.path.parent),
        "EDITARR_LOG_DIR": str(tmp_path / "logs"),
    }
    command = [sys.executable, "-m", "scanner.cli", "admin", "write-export", str(export_id)]
    result = subprocess.run(
        [*command, "--password-stdin"],
        input=OWNER_PASSWORD,
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == content
    refused = subprocess.run(
        [*command, "--password-stdin"],
        input="wrong password",
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )
    assert (
        refused.returncode == 1 and "Jellyfin did not accept the owner's password" in refused.stderr
    )
