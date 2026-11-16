# ruff: noqa: F811
"""The account read reports how far its latest link removed the old export files.

`GET /api/users/{id}` carries `link_cleanup` with counts only. It stays `removing`
across reads until recovery settles every old file, and it reports files left in
place because Editarr did not write their bytes.
"""

from __future__ import annotations

import errno

import pytest
from sqlalchemy import event

from scanner.http import users_api
from scanner.persistence.repository import export_identity_moves as files
from scanner.persistence.repository.export_regeneration import sweep_regenerations
from scanner.security.account_links import cleanup
from tests.account_link_support import (
    boundary,  # noqa: F401
    clock,  # noqa: F401
    link_app,  # noqa: F401
    prove_and_link,
    publications,
    publish_both,
)
from tests.multi_user_access_support import ADMIN, USER
from tests.test_personal_exports_http import app, review_app  # noqa: F401


@pytest.fixture
def read_app(link_app):
    link_app.client.app.include_router(users_api.router)
    return link_app


def _cleanup(app, actor=ADMIN, target=USER):
    response = app.send("GET", f"/api/users/{target}", app.accounts.unsafe_cookie(actor))
    assert response.status_code == 200, response.text
    return response.json()["link_cleanup"]


def _counts(state, *, pending=0, removed=0, absent=0, kept=0):
    return {"state": state, "pending": pending, "removed": removed, "absent": absent, "kept": kept}


def test_an_unlinked_account_has_no_link_cleanup(read_app):
    assert _cleanup(read_app) is None
    assert _cleanup(read_app, actor=USER) is None


def test_pending_removal_stays_reported_until_recovery_settles_it(read_app, monkeypatch):
    publish_both(read_app, USER, "stuck")
    paths = [path for *_, path, _content, _revision in publications(read_app, USER)]
    real = files.remove_old

    def unavailable(path, expected):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(files, "remove_old", unavailable)
    assert prove_and_link(read_app, ADMIN, USER, "Drew").json()["state"] == "removing"
    assert _cleanup(read_app) == _counts("removing", pending=2)
    assert _cleanup(read_app, actor=USER) == _counts("removing", pending=2)
    monkeypatch.setattr(files, "remove_old", real)
    with read_app.accounts.factory() as db:
        sweep_regenerations(db)
    assert _cleanup(read_app) == _counts("completed", removed=2)
    text = read_app.send("GET", f"/api/users/{USER}", read_app.accounts.unsafe_cookie(ADMIN)).text
    for path in paths:
        assert str(path.parent) not in text


def test_bytes_editarr_did_not_write_are_reported_as_kept(read_app):
    publish_both(read_app, USER, "foreign")
    publications(read_app, USER)[0][3].write_bytes(b"edited by hand\n")
    assert prove_and_link(read_app, ADMIN, USER, "Drew").json()["state"] == "completed"
    assert _cleanup(read_app) == _counts("completed", removed=1, kept=1)


def test_a_link_without_exports_reports_completed_with_no_moves(read_app):
    assert prove_and_link(read_app, ADMIN, USER, "Drew").json()["state"] == "completed"
    assert _cleanup(read_app) == _counts("completed")


def test_old_files_already_gone_are_reported_as_absent(read_app):
    publish_both(read_app, USER, "gone")
    for *_, path, _content, _revision in publications(read_app, USER):
        path.unlink()
    assert prove_and_link(read_app, ADMIN, USER, "Drew").json()["state"] == "completed"
    assert _cleanup(read_app) == _counts("completed", absent=2)


def test_the_read_counts_outcomes_without_loading_move_rows(read_app):
    publish_both(read_app, USER, "counted")
    assert prove_and_link(read_app, ADMIN, USER, "Drew").json()["state"] == "completed"
    statements = []

    def record(_conn, _cursor, statement, *_):
        statements.append(statement)

    with read_app.accounts.factory() as db:
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            assert cleanup.read(db, USER) == cleanup.LinkCleanup(
                state="completed", pending=0, removed=2, absent=0, kept=0
            )
        finally:
            event.remove(engine, "before_cursor_execute", record)
    moves = [s for s in statements if "account_link_export_moves" in s]
    assert len(moves) == 1
    assert "GROUP BY" in moves[0]
    assert "old_path" not in moves[0]
