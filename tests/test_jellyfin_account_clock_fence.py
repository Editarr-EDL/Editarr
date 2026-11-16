"""MU-16: a read begun before the clock was found stepped back never verifies an account.

The read that notices the step clears every stored verification and records
its generation as `account_settings.jellyfin_clock_fence`. A read numbered
below the fence was timed on the old clock: its verified answer changes
nothing, while a disable, a deletion, or an outage it saw still applies,
because those only reduce access. Real routers and middleware over
`live_accounts` (`mu04-access-builders-3`) and the MU-08 `JellyfinBoundary`
(`editarr-jellyfin-users-1`), on one fixed clock that tests step.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from scanner.db import UserProviderIdentityRow, UserRow
from scanner.integrations.jellyfin_user_snapshot import UserDeleted
from scanner.persistence.rows.db_auth import AccountSettingsRow
from scanner.security.access.link_state import VERIFICATION_GRACE
from scanner.security.jellyfin_accounts import admission, reads, store
from tests import test_jellyfin_accounts_http as support
from tests.jellyfin_user_boundary import IDS
from tests.media_access_support import jellyfin_user
from tests.multi_user_access_support import ADMIN
from tests.test_jellyfin_accounts_http import app as app
from tests.test_jellyfin_accounts_http import boundary as boundary
from tests.test_jellyfin_accounts_http import clock as clock

ROLLBACKS = pytest.mark.parametrize(
    "rollback", [timedelta(seconds=1), timedelta(hours=1)], ids=["1s", "1h"]
)


def _fence(app):
    return app.scalar(select(AccountSettingsRow.jellyfin_clock_fence))


def _verified_at(app, user_id):
    return app.scalar(
        select(UserProviderIdentityRow.last_verified_at).where(
            UserProviderIdentityRow.user_id == user_id
        )
    )


def _hold(monkeypatch, name):
    """Hold `name`'s avatar read, which every admission makes after Jellyfin answers."""

    entered, release = threading.Event(), threading.Event()
    original = admission.fetch_avatar

    def held(adapter, snapshot, stored_tag):
        if snapshot.name == name:
            entered.set()
            assert release.wait(10)
        return original(adapter, snapshot, stored_tag)

    monkeypatch.setattr(admission, "fetch_avatar", held)
    return entered, release


def _in_flight(target, entered, release, during):
    responses = []
    worker = threading.Thread(target=lambda: responses.append(target()))
    worker.start()
    try:
        assert entered.wait(10)
        during()
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    return responses[0]


@ROLLBACKS
def test_a_sign_in_begun_before_the_correction_cannot_restore_verification(
    app, clock, monkeypatch, rollback
):
    assert support._sign_in(app, "Avery").status_code == 200
    user_id = support._linked(app, "Avery")
    key = support._key(app, user_id)
    assert support._get(app, "/api/users", key).status_code == 200
    clock.now += rollback
    entered, release = _hold(monkeypatch, "Avery")

    def correct_the_clock():
        clock.now -= rollback
        assert support._sign_in(app, "Casey").status_code == 200
        assert _verified_at(app, user_id) is None

    response = _in_flight(
        lambda: support._sign_in(app, "Avery"), entered, release, correct_the_clock
    )

    assert (response.status_code, response.json()["code"]) == (503, "jellyfin_unavailable")
    assert _verified_at(app, user_id) is None
    corrected = clock.now
    assert support._get(app, "/api/users", key).status_code == 403
    clock.now = corrected + VERIFICATION_GRACE + timedelta(microseconds=1)
    assert support._get(app, "/api/users", key).status_code == 403
    assert support._sign_in(app, "Avery").status_code == 200
    assert support._get(app, "/api/users", key).status_code == 200


def test_the_read_that_finds_the_step_is_the_fence_and_verifies(app, clock):
    assert support._sign_in(app, "Casey").status_code == 200
    user_id = support._linked(app, "Casey")
    assert _fence(app) == 0
    with app.accounts.factory() as db:
        older = reads.begin_read(db, clock)
        clock.now -= timedelta(seconds=1)
        finding = reads.begin_read(db, clock)
        assert _fence(app) == finding.generation
        assert store.apply(db, user_id, jellyfin_user("Casey", verified_at=older.at), older) is None
        after = reads.begin_read(db, clock)
        assert _fence(app) == finding.generation
        verified = jellyfin_user("Casey", verified_at=finding.at)
        assert store.apply(db, user_id, verified, finding) is not None

    assert _verified_at(app, user_id) == finding.at.replace(tzinfo=None)
    assert after.generation > finding.generation


@pytest.mark.parametrize("seen", ["disabled", "deleted"])
def test_a_denial_read_begun_before_the_correction_still_applies(app, clock, seen):
    headers, _ = support._session(support._sign_in(app, "Casey"))
    user_id = support._linked(app, "Casey")
    key = support._key(app, user_id)
    with app.accounts.factory() as db:
        older = reads.begin_read(db, clock)
        clock.now -= timedelta(hours=1)
        reads.begin_read(db, clock)
        snapshot = jellyfin_user("Casey", verified_at=older.at)
        evidence = (
            replace(snapshot, is_disabled=True, is_administrator=True)
            if seen == "disabled"
            else UserDeleted(snapshot.identity, older.at)
        )
        assert store.apply(db, user_id, evidence, older) is not None

    user = app.scalar(select(UserRow).where(UserRow.id == user_id))
    assert (user.status, user.disable_cause, user.role) == ("disabled", f"upstream_{seen}", "user")
    assert _verified_at(app, user_id) is None
    assert support._get(app, f"/api/users/{user_id}", headers).status_code == 401
    assert support._get(app, f"/api/users/{user_id}", key).status_code == 403


def test_a_first_sign_in_begun_before_the_correction_creates_no_account(app, clock, monkeypatch):
    entered, release = _hold(monkeypatch, "Blake")

    def correct_the_clock():
        clock.now -= timedelta(seconds=1)
        assert support._sign_in(app, "Casey").status_code == 200

    response = _in_flight(
        lambda: support._sign_in(app, "Blake"), entered, release, correct_the_clock
    )

    assert (response.status_code, response.json()["code"]) == (503, "jellyfin_unavailable")
    assert support._linked(app, "Blake") is None
    assert support._sign_in(app, "Blake").status_code == 200
    assert _verified_at(app, support._linked(app, "Blake")) == clock.now.replace(tzinfo=None)


def test_an_import_listed_before_the_correction_creates_no_account(app, clock, monkeypatch):
    entered, release = _hold(monkeypatch, "Blake")

    def correct_the_clock():
        clock.now -= timedelta(seconds=1)
        with app.accounts.factory() as db:
            reads.begin_read(db, clock)

    body = {"user_ids": [IDS["Blake"]]}
    response = _in_flight(
        lambda: app.send("POST", "/api/jellyfin/users/import", ADMIN, body),
        entered,
        release,
        correct_the_clock,
    )

    assert (response.status_code, response.json()["code"]) == (503, "jellyfin_unavailable")
    assert support._linked(app, "Blake") is None
    imported = app.ok("POST", "/api/jellyfin/users/import", ADMIN, body)["results"]
    assert [result["outcome"] for result in imported] == ["imported"]
    assert _verified_at(app, support._linked(app, "Blake")) == clock.now.replace(tzinfo=None)
