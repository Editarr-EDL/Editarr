"""MU-07 per-user sessions through the real auth router and access middleware.

Accounts come from `tests.multi_user_access_support.live_accounts`
(`mu04-access-builders-3`): owner 1, admins 2 and 3, users 4 and 5. Requests
use explicit cookies so each one names exactly one browser session.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import scanner.security.auth_policy as auth_policy
from scanner.security.access.identity import AccountStatus
from scanner.security.auth_repository import (
    issue_session,
    revoke_session_by_id,
    revoke_user_sessions,
)
from scanner.settings.config import Settings
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import (
    ORIGIN,
    OWNER,
    PEER_USER,
    USER,
    live_accounts,
)

_T0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
_PAYLOAD = "/api/test-access/payload"


class _Clock:
    def __init__(self) -> None:
        self.now = _T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def accounts(tmp_path):
    live = live_accounts(tmp_path)
    yield live
    live.close()


@pytest.fixture
def serve(monkeypatch, accounts):
    """Verification-mode app that also declares the account route, as production does."""

    clients = []

    def start(configuration: Settings | None = None):
        if configuration is not None:
            accounts.configuration = configuration
        client = serve_access_fixture(monkeypatch, accounts)
        clock = _Clock()
        monkeypatch.setattr(auth_policy, "session_now", clock)
        clients.append(client)
        return client, clock

    yield start
    for client in clients:
        client.close()


def _browser(accounts, user_id: int, *, remember_me: bool = False) -> dict[str, str]:
    """Headers for one new browser session of `user_id`, issued at `_T0`, CSRF included.

    Sign-in itself is Jellyfin's (`test_jellyfin_accounts_http`); these tests exercise
    the sessions it issues.
    """

    with accounts.factory() as db:
        issued = issue_session(
            db,
            remember_me=remember_me,
            user_id=user_id,
            now=_T0,
            configuration=accounts.configuration,
        )
    return {
        **ORIGIN,
        "Cookie": f"editarr_session={issued.token}",
        "X-CSRF-Token": issued.csrf_token,
    }


def _get(client, path: str, headers: dict[str, str]):
    client.cookies.clear()
    return client.get(path, headers=headers)


def _code(response) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


def _status(client, headers: dict[str, str]) -> dict:
    return _get(client, "/api/auth/status", headers).json()


def _signed_in_as(client, headers: dict[str, str]) -> int:
    return _status(client, headers)["access"]["principal"]["user_id"]


def test_two_users_sign_in_concurrently_without_sharing_identity_or_logout(accounts, serve):
    client, _ = serve()
    owner_browser, member_browser = _browser(accounts, OWNER), _browser(accounts, USER)

    assert owner_browser["Cookie"] != member_browser["Cookie"]
    assert owner_browser["X-CSRF-Token"] != member_browser["X-CSRF-Token"]
    assert _signed_in_as(client, owner_browser) == OWNER
    assert _signed_in_as(client, member_browser) == USER
    payload = _get(client, _PAYLOAD, member_browser).json()
    assert payload["principal"]["user_id"] == USER
    assert payload["principal"]["credential"] == "session"

    crossed = {**member_browser, "X-CSRF-Token": owner_browser["X-CSRF-Token"]}
    client.cookies.clear()
    assert _code(client.post("/api/auth/logout", headers=crossed)) == (403, "csrf_failed")

    client.cookies.clear()
    signed_out = client.post("/api/auth/logout", headers=member_browser)
    assert signed_out.status_code == 200
    assert signed_out.json()["state"] == "unauthenticated"
    assert _status(client, member_browser)["state"] == "unauthenticated"
    assert _code(_get(client, _PAYLOAD, member_browser)) == (401, "authentication_required")
    assert _signed_in_as(client, owner_browser) == OWNER
    assert _get(client, "/api/protected", owner_browser).status_code == 200
    assert _get(client, _PAYLOAD, accounts.cookie(USER)).status_code == 200


def test_cross_origin_and_header_only_requests_stay_refused_per_user(accounts, serve):
    client, _ = serve()
    member = _browser(accounts, USER)

    client.cookies.clear()
    hostile = client.post(
        "/api/auth/logout", headers={**member, "Origin": "https://attacker.example"}
    )
    assert _code(hostile) == (403, "origin_failed")
    client.cookies.clear()
    no_csrf = {key: value for key, value in member.items() if key != "X-CSRF-Token"}
    assert _code(client.post("/api/auth/logout", headers=no_csrf)) == (403, "csrf_failed")
    assert _status(client, member)["state"] == "authenticated"


@pytest.mark.parametrize(
    ("remember_me", "idle", "absolute"),
    [
        (False, timedelta(minutes=30), timedelta(hours=8)),
        (True, timedelta(days=7), timedelta(days=30)),
    ],
)
def test_idle_and_absolute_boundaries_follow_the_injected_clock(
    accounts, serve, remember_me, idle, absolute
):
    client, clock = serve()
    second = timedelta(seconds=1)

    browser = _browser(accounts, USER, remember_me=remember_me)
    clock.now = _T0 + idle - second
    assert _status(client, browser)["state"] == "authenticated"
    clock.now += idle - second
    assert _status(client, browser)["state"] == "authenticated"
    clock.now += idle
    assert _status(client, browser)["state"] == "unauthenticated"
    assert _code(_get(client, _PAYLOAD, browser)) == (401, "authentication_required")

    clock.now = _T0
    browser = _browser(accounts, USER, remember_me=remember_me)
    while clock.now + idle - second < _T0 + absolute - second:
        clock.now += idle - second
        assert _get(client, _PAYLOAD, browser).status_code == 200
    clock.now = _T0 + absolute - second
    assert _get(client, _PAYLOAD, browser).status_code == 200
    clock.now = _T0 + absolute
    assert _code(_get(client, _PAYLOAD, browser)) == (401, "authentication_required")


def test_disabled_accounts_and_revoked_sessions_fail_on_the_next_request(accounts, serve):
    client, _ = serve()
    member = _browser(accounts, USER)
    peer = _browser(accounts, PEER_USER)
    assert _get(client, _PAYLOAD, member).status_code == 200

    accounts.set_status(USER, AccountStatus.DISABLED)
    assert _code(_get(client, _PAYLOAD, member)) == (403, "account_disabled")
    assert _code(_get(client, _PAYLOAD, accounts.cookie(USER))) == (403, "account_disabled")
    assert _status(client, member)["state"] == "unauthenticated"
    assert _get(client, _PAYLOAD, peer).status_code == 200

    peer_session = _status(client, peer)
    assert peer_session["state"] == "authenticated"
    with accounts.factory() as db:
        assert revoke_session_by_id(db, accounts.sessions[PEER_USER].id)
    assert _code(_get(client, _PAYLOAD, accounts.cookie(PEER_USER))) == (
        401,
        "authentication_required",
    )
    assert _get(client, _PAYLOAD, peer).status_code == 200
    with accounts.factory() as db:
        assert revoke_user_sessions(db, PEER_USER) == 1
    assert _code(_get(client, _PAYLOAD, peer)) == (401, "authentication_required")
    assert _get(client, _PAYLOAD, accounts.key(PEER_USER)).status_code == 200
    assert _get(client, "/api/protected", accounts.cookie(OWNER)).status_code == 200
