"""MU-13 HTTP harness: the real auth and key routers in front of `live_accounts`.

Accounts come from `tests.multi_user_access_support.live_accounts`
(`mu04-access-builders-3`): owner 1, admins 2 and 3, users 4 and 5, each with a
session and an API key. The app runs in verification mode with the key routes
declared evaluated, a fixed session clock, and a reauthentication limiter on an
injected monotonic clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

import scanner.http.api_key_api as api_key_api
import scanner.http.reauthentication as reauthentication
import scanner.security.auth_policy as auth_policy
from scanner.http.auth_rate_limit import LoginRateLimiter
from scanner.security.auth_audit import AuthenticationAuditLogger
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import (
    MEMBER_PASSWORD,
    ORIGIN,
    OWNER,
    OWNER_PASSWORD,
    live_accounts,
)

T0 = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def password_of(user_id: int) -> str:
    return OWNER_PASSWORD if user_id == OWNER else MEMBER_PASSWORD


def confirm(password: str | None) -> dict:
    return {} if password is None else {"reauthentication": {"password": password}}


@dataclass
class Monotonic:
    seconds: float = 1000.0

    def __call__(self) -> float:
        return self.seconds


@dataclass
class KeyApp:
    client: object
    accounts: object
    clock: Monotonic

    def send(self, method: str, path: str, headers: dict, body: dict | None = None):
        self.client.cookies.clear()
        return self.client.request(method, path, headers=headers, json=body)

    def reveal(self, headers: dict, password: str | None, holder: int | None = None):
        path = (
            "/api/auth/api-key/reveal" if holder is None else f"/api/users/{holder}/api-key/reveal"
        )
        return self.send("POST", path, headers, confirm(password))

    def issue(self, headers: dict, password: str | None, holder: int | None = None):
        path = "/api/auth/api-key" if holder is None else f"/api/users/{holder}/api-key"
        return self.send("POST", path, headers, confirm(password))

    def revoke(self, headers: dict, holder: int | None = None):
        path = "/api/auth/api-key" if holder is None else f"/api/users/{holder}/api-key"
        return self.send("DELETE", path, headers)

    def works(self, key: str) -> int:
        return self.send("GET", "/api/test-access/payload", {"X-Api-Key": key}).status_code


def code(response) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


@pytest.fixture
def accounts(tmp_path):
    live = live_accounts(tmp_path)
    yield live
    live.close()


@pytest.fixture
def key_app(monkeypatch, accounts, tmp_path):
    clients = []

    def start() -> KeyApp:
        clock = Monotonic()
        limiter = LoginRateLimiter(configuration=accounts.configuration, clock=clock)
        monkeypatch.setattr(
            reauthentication,
            "reauthentication_guard",
            reauthentication.ReauthenticationGuard(limiter),
        )
        monkeypatch.setattr(
            api_key_api, "reauthentication_guard", reauthentication.reauthentication_guard
        )
        client = serve_access_fixture(monkeypatch, accounts)
        client.app.include_router(api_key_api.router)
        app = KeyApp(client, accounts, clock)

        client.app.state.authentication_audit = AuthenticationAuditLogger(tmp_path / "logs", now=T0)
        monkeypatch.setattr(auth_policy, "session_now", lambda: T0)
        clients.append(client)
        return app

    yield start
    for client in clients:
        client.close()


__all__ = ["ORIGIN", "accounts", "key_app"]
