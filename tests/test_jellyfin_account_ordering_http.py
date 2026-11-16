"""MU-16 HTTP: a Jellyfin sign-in gets a session only from a read no newer change has passed.

Real routers and middleware over `live_accounts` (`mu04-access-builders-3`) and
the MU-08 `JellyfinBoundary` (`editarr-jellyfin-users-1`), on one fixed clock
that tests also step backward.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select

import scanner.http.jellyfin_login_api as login_api
from scanner.db import AuthSessionRow, JellyfinAccountSyncRow, UserRow
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security.access.link_state import VERIFICATION_GRACE
from scanner.security.jellyfin_accounts import admission, reads, store
from tests import test_jellyfin_accounts_http as support
from tests.jellyfin_user_boundary import IDS
from tests.media_access_support import jellyfin_user
from tests.multi_user_access_support import ADMIN
from tests.test_jellyfin_accounts_http import app as app
from tests.test_jellyfin_accounts_http import boundary as boundary
from tests.test_jellyfin_accounts_http import clock as clock


def _open_sessions(app, user_id):
    return app.scalar(
        select(AuthSessionRow.id).where(
            AuthSessionRow.user_id == user_id, AuthSessionRow.revoked_at.is_(None)
        )
    )


def _all_sessions(app, user_id):
    return app.scalar(
        select(func.count()).select_from(AuthSessionRow).where(AuthSessionRow.user_id == user_id)
    )


def _open_sessions(app, user_id):
    return app.scalar(
        select(AuthSessionRow.id).where(
            AuthSessionRow.user_id == user_id, AuthSessionRow.revoked_at.is_(None)
        )
    )


def test_no_writer_can_commit_between_admitting_a_sign_in_and_issuing_its_session(app, monkeypatch):
    assert support._sign_in(app, "Casey").status_code == 200
    original = login_api.issue_session
    writable = []

    def issue(*args, **kwargs):
        other = sqlite3.connect(app.accounts.path, timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
            writable.append(True)
        except sqlite3.OperationalError:
            writable.append(False)
        finally:
            other.close()
        return original(*args, **kwargs)

    monkeypatch.setattr(login_api, "issue_session", issue)

    assert support._sign_in(app, "Casey").status_code == 200
    assert writable == [False]


def test_a_sign_in_read_before_a_disable_and_re_enable_gets_no_session(app, clock, monkeypatch):
    old_headers, _ = support._session(support._sign_in(app, "Casey"))
    user_id = support._linked(app, "Casey")
    read, release = threading.Event(), threading.Event()
    original = admission.fetch_avatar

    def held(*args):
        read.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(admission, "fetch_avatar", held)
    responses = []
    worker = threading.Thread(target=lambda: responses.append(support._sign_in(app, "Casey")))
    worker.start()
    try:
        assert read.wait(10)
        with app.accounts.factory() as db:
            clock.now += timedelta(seconds=1)
            disabled = replace(jellyfin_user("Casey", verified_at=clock()), is_disabled=True)
            store.apply(db, user_id, disabled, reads.begin_read(db, clock))
            clock.now += timedelta(seconds=1)
            enabled = jellyfin_user("Casey", verified_at=clock())
            store.apply(db, user_id, enabled, reads.begin_read(db, clock))
    finally:
        release.set()
        worker.join(10)

    assert [response.status_code for response in responses] == [503]
    assert responses[0].json()["code"] == "jellyfin_unavailable"
    assert _open_sessions(app, user_id) is None
    assert support._get(app, f"/api/users/{user_id}", old_headers).status_code == 401
    assert support._sign_in(app, "Casey").status_code == 200


STEPS = pytest.mark.parametrize(
    "step", [timedelta(), -timedelta(seconds=1), -timedelta(hours=1)], ids=["same", "-1s", "-1h"]
)


@STEPS
def test_a_disable_read_after_the_clock_steps_back_blocks_the_session_and_key(
    app, boundary, clock, step
):
    headers, _ = support._session(support._sign_in(app, "Drew"))
    user_id = support._linked(app, "Drew")
    key = support._key(app, user_id)
    clock.now += step
    boundary.state = "changed"
    support._sync(app)

    assert support._get(app, f"/api/users/{user_id}", headers).status_code == 401
    assert support._get(app, f"/api/users/{user_id}", key).status_code == 403
    assert app.scalar(select(UserRow.status).where(UserRow.id == user_id)) == "disabled"


@STEPS
@pytest.mark.parametrize(
    ("name", "confirmed"), [("Drew", ("disabled", False)), ("Avery", ("active", False))]
)
def test_a_delayed_sign_in_never_undoes_a_newer_disable_or_snapshot_whatever_the_clock_says(
    app, boundary, clock, monkeypatch, step, name, confirmed
):
    assert support._sign_in(app, name).status_code == 200
    user_id = support._linked(app, name)
    clock.now += timedelta(minutes=1)
    read, release = threading.Event(), threading.Event()
    original = admission.fetch_avatar

    def held(*args):
        read.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(admission, "fetch_avatar", held)
    responses = []
    worker = threading.Thread(target=lambda: responses.append(support._sign_in(app, name)))
    worker.start()
    try:
        assert read.wait(10)
        clock.now += step
        boundary.state = "changed"
        support._sync(app)
        issued = _all_sessions(app, user_id)
    finally:
        release.set()
        worker.join(10)

    with app.accounts.factory() as db:
        user = db.get(UserRow, user_id)
        assert (user.status, db.get(JellyfinAccountSyncRow, user_id).is_administrator) == confirmed
    assert [response.status_code for response in responses] == [503]
    assert _all_sessions(app, user_id) == issued


def test_a_clock_step_back_never_lengthens_last_verified_access(app, boundary, clock):
    app.ok("POST", "/api/jellyfin/users/import", ADMIN, {"user_ids": [IDS["Avery"]]})
    avery = support._linked(app, "Avery")
    key = support._key(app, avery)
    boundary.fault = "server-error"
    clock.now += VERIFICATION_GRACE - timedelta(hours=1)
    support._sync(app)
    assert support._get(app, "/api/users", key).status_code == 200

    clock.now -= timedelta(hours=12)
    assert support._get(app, "/api/users", key).status_code == 403
    support._sync(app)
    assert support._get(app, "/api/users", key).status_code == 403
    assert support._get(app, f"/api/users/{avery}", key).status_code == 200
    clock.now += timedelta(hours=12)
    assert support._get(app, "/api/users", key).status_code == 403

    boundary.fault = None
    support._sync(app)
    assert support._get(app, "/api/users", key).status_code == 200


def _hold_first_answer(monkeypatch, method):
    """Hold the first answer of `JellyfinUserAdapter.<method>` until released."""

    answered, release = threading.Event(), threading.Event()
    original = getattr(JellyfinUserAdapter, method)

    def held(self, *args):
        answer = original(self, *args)
        if not answered.is_set():
            answered.set()
            assert release.wait(10)
        return answer

    monkeypatch.setattr(JellyfinUserAdapter, method, held)
    return answered, release


def test_a_sign_in_is_ordered_from_before_jellyfin_answers_it(app, boundary, monkeypatch):
    assert support._sign_in(app, "Drew").status_code == 200
    user_id = support._linked(app, "Drew")
    answered, release = _hold_first_answer(monkeypatch, "authenticate")
    responses = []
    worker = threading.Thread(target=lambda: responses.append(support._sign_in(app, "Drew")))
    worker.start()
    try:
        assert answered.wait(10)
        boundary.state = "changed"
        support._sync(app)
    finally:
        release.set()
        worker.join(10)

    assert [response.status_code for response in responses] == [503]
    assert app.scalar(select(UserRow.status).where(UserRow.id == user_id)) == "disabled"


def test_an_import_is_ordered_from_before_jellyfin_lists_its_users(app, boundary, monkeypatch):
    body = {"user_ids": [IDS["Avery"]]}
    app.ok("POST", "/api/jellyfin/users/import", ADMIN, body)
    user_id = support._linked(app, "Avery")
    administrator = select(JellyfinAccountSyncRow.is_administrator).where(
        JellyfinAccountSyncRow.user_id == user_id
    )
    assert app.scalar(administrator) is True
    listed, release = _hold_first_answer(monkeypatch, "users")
    responses = []
    worker = threading.Thread(
        target=lambda: responses.append(app.send("POST", "/api/jellyfin/users/import", ADMIN, body))
    )
    worker.start()
    try:
        assert listed.wait(10)
        boundary.state = "changed"
        support._sync(app)
    finally:
        release.set()
        worker.join(10)

    assert [response.json()["results"][0]["outcome"] for response in responses] == ["existing"]
    assert app.scalar(administrator) is False
    assert app.scalar(select(UserRow.role).where(UserRow.id == user_id)) == "admin"
