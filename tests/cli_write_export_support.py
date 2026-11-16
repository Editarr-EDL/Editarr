"""Shared setup for the `editarr admin write-export` tests.

`published` serves one personal EDL whose owner is linked to Avery on a loopback
Jellyfin stand-in, with `editarr admin` pointed at the app's data directory.
"""

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scanner import cli
from scanner.commands import admin
from scanner.db import (
    AuditEventRow,
    JellyfinSettingsRow,
    MediaItemExportRow,
)
from scanner.integrations.jellyfin_user_snapshot import parse_user
from tests.cli_support import _run
from tests.jellyfin_user_boundary import IDS, PRIMARY, JellyfinBoundary, user_dto
from tests.multi_user_access_support import MEMBER_PASSWORD, OWNER, OWNER_PASSWORD, USER
from tests.test_personal_exports_http import app, publish, review_app, seeded_review

__all__ = ["app", "jellyfin", "published", "review_app"]


def _published(app, monkeypatch, name):
    monkeypatch.setattr(admin, "init_db", lambda: None)
    monkeypatch.setattr(admin, "get_session_factory", lambda: app.accounts.factory)
    monkeypatch.setattr(cli.settings, "data_dir", app.accounts.path.parent)
    monkeypatch.setattr(cli.settings, "log_dir", app.accounts.path.parent / "logs")
    review = seeded_review(app, USER, name)
    export_id = publish(app, review, "edl").json()["id"]
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, export_id)
        path, content = Path(row.destination_path), row.content_bytes
    return export_id, path, content


@pytest.fixture
def jellyfin(app):
    """The owner linked to Avery on a loopback Jellyfin stand-in, whose password is
    `OWNER_PASSWORD`; Blake's is `MEMBER_PASSWORD`."""
    passwords = {IDS["Avery"]: OWNER_PASSWORD, IDS["Blake"]: MEMBER_PASSWORD}
    with JellyfinBoundary(secrets.token_urlsafe(24), passwords) as boundary:
        with app.accounts.factory() as db:
            db.merge(JellyfinSettingsRow(id=1, server_url=boundary.url, api_key=boundary.api_key))
            db.commit()
        snapshot = parse_user(user_dto("Avery"), PRIMARY, datetime.now(UTC))
        app.accounts.link_jellyfin_user(OWNER, snapshot)
        yield boundary


@pytest.fixture
def published(app, jellyfin, monkeypatch):
    """One served personal EDL, with `editarr admin` pointed at the app's data directory."""
    return _published(app, monkeypatch, "write-export")


def _write(monkeypatch, export_id, password=OWNER_PASSWORD):
    _run(monkeypatch, ["admin", "write-export", str(export_id), "--password-stdin"], stdin=password)


def _refused(monkeypatch, capsys, export_id, password=OWNER_PASSWORD) -> str:
    with pytest.raises(SystemExit) as exit_info:
        _write(monkeypatch, export_id, password)
    assert exit_info.value.code == 1
    output = capsys.readouterr()
    assert output.out == "" and (not password or password not in output.err)
    return output.err


def _events(app):
    with app.accounts.factory() as db:
        return [
            (row.outcome, row.actor_id, json.loads(row.details_json).get("reason"))
            for row in db.query(AuditEventRow)
            .filter_by(action="export.write_committed")
            .order_by(AuditEventRow.occurred_at)
        ]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
