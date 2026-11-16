"""MU-10 audit events through the real auth routers: attribution, secrets, throttles, failures.

Accounts come from `tests.multi_user_access_support.live_accounts`
(`mu04-access-builders-3`): owner 1, admins 2 and 3, users 4 and 5. Users 4 and 5
are linked to Jellyfin users Blake and Casey on the MU-08 `JellyfinBoundary`, a
loopback stand-in for Jellyfin. Each app gets a fixed session clock and its own
authentication log. Sentinel strings stand in for every secret a request carries;
none may appear in a stored row or log line.
"""

from __future__ import annotations

import json
import secrets as token_source
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

import scanner.http.jellyfin_login_api as jellyfin_login_api
import scanner.security.auth_policy as auth_policy
from scanner.db import (
    AuditEventRow,
    AuthSessionRow,
    JellyfinSettingsRow,
    UserProviderIdentityRow,
)
from scanner.http.auth_rate_limit import LoginRateLimiter
from scanner.security.access import link_state
from scanner.security.access.identity import AccountStatus
from scanner.security.audit import store
from scanner.security.auth_audit import AuthenticationAuditLogger
from scanner.settings.config import Settings
from tests.jellyfin_user_boundary import IDS, PRIMARY, JellyfinBoundary
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import (
    ORIGIN,
    OWNER,
    OWNER_PASSWORD,
    PEER_USER,
    USER,
    live_accounts,
)

_T0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
WRONG = "sentinel-wrong-password-7f3a"
TYPED = "sentinel-typed-secret-55aa"
PASSWORDS = {IDS[name]: f"sentinel-jellyfin-{name.lower()}" for name in IDS}
LINKED = {USER: "Blake", PEER_USER: "Casey"}
_COLUMNS = (
    AuditEventRow.action,
    AuditEventRow.outcome,
    AuditEventRow.actor_kind,
    AuditEventRow.actor_id,
    AuditEventRow.credential,
    AuditEventRow.target_user_id,
    AuditEventRow.details_json,
)


@pytest.fixture
def boundary():
    with JellyfinBoundary(token_source.token_urlsafe(24), PASSWORDS) as server:
        yield server


@pytest.fixture
def accounts(tmp_path, boundary):
    live = live_accounts(tmp_path)
    with live.factory() as db:
        db.add(JellyfinSettingsRow(id=1, server_url=boundary.url, api_key=boundary.api_key))
        for user_id, name in LINKED.items():
            db.add(
                UserProviderIdentityRow(
                    user_id=user_id,
                    server_id=PRIMARY,
                    provider_user_id=IDS[name],
                    link_state="verified",
                    last_verified_at=_T0,
                )
            )
        db.commit()
    yield live
    live.close()


@pytest.fixture
def serve(monkeypatch, accounts, tmp_path):
    clients = []

    def start(*, limiter=None):
        client = serve_access_fixture(monkeypatch, accounts)
        client.app.include_router(jellyfin_login_api.router)
        client.app.state.authentication_audit = AuthenticationAuditLogger(
            tmp_path / "logs", now=_T0
        )
        monkeypatch.setattr(
            jellyfin_login_api, "jellyfin_login_limiter", limiter or LoginRateLimiter()
        )
        monkeypatch.setattr(auth_policy, "session_now", lambda: _T0)
        monkeypatch.setattr(link_state, "link_clock", lambda: _T0)
        clients.append(client)
        return client

    yield start
    for client in clients:
        client.close()


def _baseline(accounts) -> int:
    with accounts.factory() as db:
        return db.scalar(select(func.coalesce(func.max(AuditEventRow.id), 0)))


def _events(accounts, after: int, action: str | None = None) -> list[tuple]:
    with accounts.factory() as db:
        query = select(*_COLUMNS).where(AuditEventRow.id > after)
        if action is not None:
            query = query.where(AuditEventRow.action == action)
        return [tuple(row) for row in db.execute(query.order_by(AuditEventRow.id))]


def _stored_text(accounts) -> str:
    with accounts.factory() as db:
        rows = db.execute(select(AuditEventRow.__table__)).mappings()
        return json.dumps([dict(row) for row in rows], default=str)


def _log_records(tmp_path) -> list[dict]:
    path = tmp_path / "logs" / "auth-audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()][1:]


def _sign_in(client, username: str, password: str):
    client.cookies.clear()
    return client.post(
        "/api/auth/jellyfin/login",
        headers=ORIGIN,
        json={"username": username, "password": password, "remember_me": False},
    )


def _password(user_id: int) -> str:
    return PASSWORDS[IDS[LINKED[user_id]]]


def _assert_no_secret(text: str, *secrets: str) -> None:
    leaked = [secret for secret in secrets if secret and secret in text]
    assert leaked == []


def test_the_stored_secret_check_sees_a_value_planted_in_a_stored_column(accounts, serve):
    planted = "sentinel-planted-secret-0c4d"
    assert _sign_in(serve(), LINKED[USER], _password(USER)).status_code == 200
    with accounts.factory() as db:
        db.execute(update(AuditEventRow).values(details_json=f'{{"reason":"{planted}"}}'))
        db.commit()

    with pytest.raises(AssertionError):
        _assert_no_secret(_stored_text(accounts), planted)


def test_sign_in_outcomes_are_attributed_and_logged_once_each(accounts, serve, tmp_path):
    client = serve()
    before = _baseline(accounts)
    accounts.set_status(PEER_USER, AccountStatus.DISABLED)

    signed_in = _sign_in(client, LINKED[USER], _password(USER))
    statuses = [
        signed_in.status_code,
        _sign_in(client, LINKED[USER], WRONG).status_code,
        _sign_in(client, TYPED, WRONG).status_code,
        _sign_in(client, LINKED[PEER_USER], _password(PEER_USER)).status_code,
    ]

    assert statuses == [200, 401, 401, 403]
    assert _events(accounts, before, "auth.sign_in") == [
        ("auth.sign_in", "succeeded", "user", USER, "session", USER,
         '{"method":"password","remember_me":false}'),
        ("auth.sign_in", "failed", "anonymous", None, None, None,
         '{"method":"password","reason":"invalid_credentials"}'),
        ("auth.sign_in", "failed", "anonymous", None, None, None,
         '{"method":"password","reason":"invalid_credentials"}'),
        ("auth.sign_in", "denied", "anonymous", None, None, PEER_USER,
         '{"method":"password","reason":"account_disabled"}'),
    ]  # fmt: skip
    session = signed_in.cookies["editarr_session"]
    secrets = (WRONG, TYPED, _password(USER), session, signed_in.json()["csrf_token"])
    _assert_no_secret(_stored_text(accounts), *secrets)
    _assert_no_secret((tmp_path / "logs" / "auth-audit.jsonl").read_text(), *secrets)


def test_each_throttled_request_is_logged_but_only_each_lockout_is_stored(
    accounts, serve, tmp_path
):
    elapsed = [0.0]
    limiter = LoginRateLimiter(
        configuration=Settings(data_dir=tmp_path, auth_login_username_attempts=2),
        clock=lambda: elapsed[0],
    )
    client = serve(limiter=limiter)
    before = _baseline(accounts)

    first = [_sign_in(client, LINKED[USER], WRONG).status_code for _ in range(5)]
    elapsed[0] += 16 * 60
    second = [_sign_in(client, LINKED[USER], WRONG).status_code for _ in range(4)]

    assert first == [401, 401, 429, 429, 429]
    assert second == [401, 401, 429, 429]
    failed = ("auth.sign_in", "failed", "anonymous", None, None, None,
              '{"method":"password","reason":"invalid_credentials"}')  # fmt: skip
    throttled = ("auth.sign_in", "denied", "anonymous", None, None, None,
                 '{"method":"password","reason":"throttled"}')  # fmt: skip
    assert _events(accounts, before) == [failed, failed, throttled, failed, failed, throttled]
    refusals = [r["event"] for r in _log_records(tmp_path)]
    assert refusals.count("auth.login_throttled") == 5
    assert refusals.count("auth.login") == 4
    _assert_no_secret(_stored_text(accounts), WRONG)


def test_api_key_issue_and_revocation_are_attributed_without_the_key(accounts, serve):
    client = serve()
    owner = accounts.unsafe_cookie(OWNER)
    before = _baseline(accounts)

    client.cookies.clear()
    issued = client.post(
        "/api/auth/api-key",
        headers=owner,
        json={"reauthentication": {"password": OWNER_PASSWORD}},
    )
    client.cookies.clear()
    revoked = client.delete("/api/auth/api-key", headers={"X-Api-Key": issued.json()["api_key"]})
    client.cookies.clear()
    absent = client.delete("/api/auth/api-key", headers=owner)

    assert [r.status_code for r in (issued, revoked, absent)] == [200] * 3
    assert _events(accounts, before) == [
        ("api_key.issue", "succeeded", "user", OWNER, "session", OWNER, '{"replaced":true}'),
        ("api_key.revoke", "succeeded", "user", OWNER, "api_key", OWNER, '{"revoked":true}'),
        ("api_key.revoke", "succeeded", "user", OWNER, "session", OWNER, '{"revoked":false}'),
    ]
    _assert_no_secret(_stored_text(accounts), issued.json()["api_key"], accounts.keys[OWNER])


def _locked(*args, **kwargs):
    raise OperationalError("DELETE", {}, Exception("database is locked"))


def _session_count(accounts, user_id: int) -> int:
    with accounts.factory() as db:
        return db.scalar(
            select(func.count())
            .select_from(AuthSessionRow)
            .where(AuthSessionRow.user_id == user_id)
        )


def test_an_audit_storage_failure_never_admits_or_commits_an_unaudited_change(
    accounts, serve, monkeypatch, caplog
):
    client = serve()
    owner = accounts.unsafe_cookie(OWNER)
    before, sessions = _baseline(accounts), _session_count(accounts, USER)
    monkeypatch.setattr(store, "expire_events", _locked)

    refused = _sign_in(client, LINKED[USER], WRONG)
    with pytest.raises(OperationalError):
        _sign_in(client, LINKED[USER], _password(USER))
    client.cookies.clear()
    with pytest.raises(OperationalError):
        client.post(
            "/api/auth/api-key",
            headers=owner,
            json={"reauthentication": {"password": OWNER_PASSWORD}},
        )

    assert refused.status_code == 401
    assert _session_count(accounts, USER) == sessions
    assert _events(accounts, before) == []
    monkeypatch.setattr(store, "expire_events", lambda db, *, now: 0)
    client.cookies.clear()
    assert client.get("/api/test-access/payload", headers=accounts.key(OWNER)).status_code == 200
    assert _sign_in(client, LINKED[USER], _password(USER)).status_code == 200
    _assert_no_secret(caplog.text, WRONG, _password(USER), accounts.keys[OWNER])
