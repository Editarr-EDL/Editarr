"""MU-13 fresh-authentication guard and write contention through HTTP.

Harness: `tests.api_key_http_support`. The reauthentication limiter runs on an
injected monotonic clock and the session clock is fixed, so windows are exact.
Jellyfin's answer is the `live_accounts` stand-in, except where a test asks the
MU-08 `JellyfinBoundary` on loopback.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import scanner.http.reauthentication as reauthentication
from scanner.db import AuditEventRow, JellyfinSettingsRow
from scanner.http.auth_rate_limit import LoginRateLimiter
from scanner.http.reauthentication import ReauthenticationGuard
from scanner.integrations.jellyfin_user_snapshot import parse_user
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.records import AuditOutcome
from scanner.security.access.request_access import install_access_errors
from scanner.security.jellyfin_accounts.reauthentication import check_linked_password
from tests.api_key_http_support import accounts as accounts
from tests.api_key_http_support import code
from tests.api_key_http_support import key_app as key_app
from tests.jellyfin_user_boundary import IDS, PRIMARY, JellyfinBoundary, user_dto
from tests.multi_user_access_support import (
    MEMBER_PASSWORD,
    OWNER,
    OWNER_PASSWORD,
    PEER_USER,
    USER,
)
from tests.users_api_http_support import users_app as users_app

WRONG = "sentinel-wrong-reauth-6b1e"


def _events(accounts) -> list[tuple]:
    with accounts.factory() as db:
        return [
            tuple(row)
            for row in db.execute(
                select(
                    AuditEventRow.action,
                    AuditEventRow.outcome,
                    AuditEventRow.actor_id,
                    AuditEventRow.credential,
                    AuditEventRow.target_user_id,
                    AuditEventRow.details_json,
                ).order_by(AuditEventRow.id)
            )
        ]


def test_wrong_passwords_throttle_one_account_until_the_window_passes(accounts, key_app):
    app = key_app()
    limit = accounts.configuration.auth_login_username_attempts
    window = accounts.configuration.auth_login_window_seconds
    holder = accounts.unsafe_cookie(USER)
    for _ in range(limit):
        assert code(app.reveal(holder, WRONG)) == (403, "reauthentication_failed")
    throttled = app.reveal(holder, MEMBER_PASSWORD)
    assert code(throttled) == (429, "reauthentication_throttled")
    assert throttled.headers.get("retry-after") == str(window)
    assert throttled.headers["cache-control"] == "no-store"
    peer = app.reveal(accounts.unsafe_cookie(PEER_USER), MEMBER_PASSWORD)
    assert peer.json()["api_key"] == accounts.keys[PEER_USER]

    app.clock.seconds += window - 0.25
    throttled = app.reveal(holder, MEMBER_PASSWORD)
    assert code(throttled) == (429, "reauthentication_throttled")
    assert throttled.headers.get("retry-after") == "1"
    app.clock.seconds += 0.25
    assert app.reveal(holder, MEMBER_PASSWORD).json()["api_key"] == accounts.keys[USER]


def test_a_success_clears_the_account_failures(accounts, key_app):
    app = key_app()
    limit = accounts.configuration.auth_login_username_attempts
    holder = accounts.unsafe_cookie(USER)
    for _ in range(limit - 1):
        app.reveal(holder, WRONG)
    assert app.reveal(holder, MEMBER_PASSWORD).status_code == 200
    for _ in range(limit - 1):
        assert code(app.reveal(holder, WRONG)) == (403, "reauthentication_failed")
    assert app.reveal(holder, MEMBER_PASSWORD).status_code == 200


def test_global_denial_handler_preserves_retry_headers():
    app = FastAPI()
    install_access_errors(app)

    @app.get("/denied")
    def denied():
        error = AccessDenied(AccessErrorCode.REAUTHENTICATION_THROTTLED)
        error.headers["Retry-After"] = "17"
        raise error

    with TestClient(app) as client:
        response = client.get("/denied")
    assert code(response) == (429, "reauthentication_throttled")
    assert response.headers.get("retry-after") == "17"
    assert response.headers["cache-control"] == "no-store"


def test_user_mutations_preserve_the_guards_remaining_window(accounts, users_app, monkeypatch):
    import scanner.http.account_operations as account_operations
    import scanner.http.users_access as users_access

    seconds = [1000.0]
    guard = ReauthenticationGuard(
        LoginRateLimiter(configuration=accounts.configuration, clock=lambda: seconds[0])
    )
    monkeypatch.setattr(users_access, "reauthentication_guard", guard)
    monkeypatch.setattr(account_operations, "reauthentication_guard", guard)
    for _ in range(accounts.configuration.auth_login_username_attempts):
        failed = users_app.send(
            "PUT",
            f"/api/users/{USER}/role",
            OWNER,
            {"role": "admin", "reauthentication": {"password": WRONG}},
        )
        assert code(failed) == (403, "reauthentication_failed")
    seconds[0] += accounts.configuration.auth_login_window_seconds - 3.5
    blocked = users_app.send(
        "PUT",
        f"/api/users/{USER}/role",
        OWNER,
        {"role": "admin", "reauthentication": {"password": OWNER_PASSWORD}},
    )
    assert code(blocked) == (429, "reauthentication_throttled")
    assert blocked.headers.get("retry-after") == "4"


def test_retry_window_waits_for_both_buckets_and_for_enough_failures_to_expire(
    accounts, monkeypatch
):
    monkeypatch.setattr(accounts.configuration, "auth_login_username_attempts", 2)
    monkeypatch.setattr(accounts.configuration, "auth_login_address_attempts", 3)
    monkeypatch.setattr(accounts.configuration, "auth_login_window_seconds", 10)
    seconds = [0.0]
    limiter = LoginRateLimiter(configuration=accounts.configuration, clock=lambda: seconds[0])
    for moment, username, address in [
        (0, "alpha", "other"),
        (1, "alpha", "other"),
        (2, "beta", "shared"),
        (3, "gamma", "shared"),
        (4, "delta", "shared"),
    ]:
        seconds[0] = moment
        limiter.record_failure(username=username, address=address)
    assert limiter.retry_after(username="alpha", address="shared") == 8
    assert limiter.retry_after(username="alpha", address="unused") == 6
    assert limiter.retry_after(username="unused", address="unused") == 0
    seconds[0] = 5
    limiter.record_failure(username="epsilon", address="shared")
    assert limiter.retry_after(username="unused", address="shared") == 8
    seconds[0] = 12
    assert limiter.retry_after(username="unused", address="shared") == 1
    seconds[0] = 13
    assert limiter.allowed(username="unused", address="shared")


def test_refusals_never_reach_the_password_check(accounts, key_app):
    """A denied capability or an API key is refused without spending a throttle attempt."""

    app = key_app()
    limit = accounts.configuration.auth_login_username_attempts
    member = accounts.unsafe_cookie(USER)
    for _ in range(limit + 2):
        assert code(app.reveal(member, WRONG, holder=PEER_USER)) == (404, "not_found")
        assert code(app.reveal(accounts.key(USER), WRONG)) == (403, "interactive_session_required")
    assert app.reveal(member, MEMBER_PASSWORD).status_code == 200


def test_key_actions_are_audited_without_any_secret(accounts, key_app, tmp_path, caplog):
    app = key_app()
    before = len(_events(accounts))
    owner = accounts.unsafe_cookie(OWNER)
    issued = app.issue(owner, OWNER_PASSWORD).json()["api_key"]
    app.reveal(owner, OWNER_PASSWORD, holder=USER)
    app.reveal(accounts.unsafe_cookie(USER), WRONG)
    app.revoke(accounts.key(USER))

    assert _events(accounts)[before:] == [
        ("api_key.issue", "succeeded", OWNER, "session", OWNER, '{"replaced":true}'),
        ("api_key.reveal", "succeeded", OWNER, "session", USER, "{}"),
        ("api_key.reveal", "failed", USER, "session", USER, '{"reason":"reauthentication_failed"}'),
        ("api_key.revoke", "succeeded", USER, "api_key", USER, '{"revoked":true}'),
    ]
    with accounts.factory() as db:
        stored = json.dumps(
            [dict(row) for row in db.execute(select(AuditEventRow.__table__)).mappings()],
            default=str,
        )
    logs = "".join(path.read_text() for path in (tmp_path / "logs").glob("*") if path.is_file())
    secrets = [
        issued,
        accounts.keys[USER],
        accounts.keys[OWNER],
        OWNER_PASSWORD,
        MEMBER_PASSWORD,
        WRONG,
        accounts.sessions[OWNER].token,
        accounts.sessions[OWNER].csrf_token,
        accounts.sessions[USER].token,
    ]
    for text in (stored, logs, caplog.text):
        assert [secret for secret in secrets if secret in text] == []


def test_an_exhausted_busy_wait_during_rotation_changes_nothing(accounts, key_app):
    engine = create_engine(
        f"sqlite:///{accounts.path}", connect_args={"check_same_thread": False, "timeout": 0.2}
    )
    accounts.factory = sessionmaker(bind=engine)
    app = key_app()
    holder = sqlite3.connect(accounts.path, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        busy = app.issue(accounts.unsafe_cookie(USER), MEMBER_PASSWORD)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert code(busy) == (503, "database_busy")
    assert busy.headers["retry-after"] == "1"
    assert app.works(accounts.keys[USER]) == 200
    with accounts.factory() as db:
        issues = db.scalar(
            select(func.count())
            .select_from(AuditEventRow)
            .where(
                AuditEventRow.action == "api_key.issue",
                AuditEventRow.outcome == AuditOutcome.SUCCEEDED.value,
            )
        )
    assert issues == 0
    engine.dispose()


@pytest.mark.parametrize("body", [{"reauthentication": {"password": ""}}, {"password": "x"}])
def test_malformed_confirmations_are_rejected_before_any_check(accounts, key_app, body):
    app = key_app()
    response = app.send("POST", "/api/auth/api-key/reveal", accounts.unsafe_cookie(USER), body)
    assert response.status_code in {403, 422}
    assert "api_key" not in response.json()


def test_jellyfin_checks_the_password_and_an_unlinked_account_cannot_reauthenticate(
    accounts, key_app, monkeypatch
):
    passwords = {IDS["Blake"]: "jf-blake-password"}
    with JellyfinBoundary(secrets.token_urlsafe(24), passwords) as boundary:
        with accounts.factory() as db:
            db.add(JellyfinSettingsRow(id=1, server_url=boundary.url, api_key=boundary.api_key))
            db.commit()
        accounts.link_jellyfin_user(USER, parse_user(user_dto("Blake"), PRIMARY, datetime.now(UTC)))
        app = key_app()
        monkeypatch.setattr(reauthentication, "check_linked_password", check_linked_password)

        member, peer = accounts.unsafe_cookie(USER), accounts.unsafe_cookie(PEER_USER)
        assert code(app.reveal(member, MEMBER_PASSWORD)) == (403, "reauthentication_failed")
        assert app.reveal(member, "jf-blake-password").json()["api_key"] == accounts.keys[USER]
        assert code(app.reveal(peer, "jf-blake-password")) == (403, "reauthentication_failed")


def test_an_unreachable_jellyfin_is_not_a_wrong_password_and_never_throttles(accounts, key_app):
    passwords = {IDS["Blake"]: "jf-blake-password"}
    with JellyfinBoundary(secrets.token_urlsafe(24), passwords) as boundary:
        with accounts.factory() as db:
            db.add(JellyfinSettingsRow(id=1, server_url=boundary.url, api_key=boundary.api_key))
            db.commit()
        accounts.link_jellyfin_user(USER, parse_user(user_dto("Blake"), PRIMARY, datetime.now(UTC)))
        app = key_app()
        member = accounts.unsafe_cookie(USER)
        with accounts.factory() as db:
            db.get(JellyfinSettingsRow, 1).server_url = "http://127.0.0.1:9"
            db.commit()
        for _ in range(accounts.configuration.auth_login_username_attempts + 2):
            outage = app.reveal(member, "jf-blake-password")
            assert code(outage) == (503, "jellyfin_unavailable")
            assert "api_key" not in outage.json()
        with accounts.factory() as db:
            db.get(JellyfinSettingsRow, 1).server_url = boundary.url
            db.commit()
        assert app.reveal(member, "jf-blake-password").json()["api_key"] == accounts.keys[USER]
    reasons = [json.loads(row[5]).get("reason") for row in _events(accounts)]
    assert "reauthentication_failed" not in reasons
