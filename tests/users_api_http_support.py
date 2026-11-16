"""MU-15 HTTP harness: the real Users routers in front of `live_accounts`.

Accounts come from `tests.multi_user_access_support.live_accounts`
(`mu04-access-builders-3`): owner 1, admins 2 and 3, users 4 and 5, each with a
session and an API key. The app runs the real authentication middleware in
verification mode, declares the MU-15 routes evaluated as production does, and
uses a reauthentication limiter of its own. Reauthentication passwords are the
stand-in Jellyfin passwords of `live_accounts`.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pytest
from PIL import Image
from sqlalchemy import select

import scanner.http.account_operations as account_operations
import scanner.http.users_access as users_access
from scanner.http.auth_rate_limit import LoginRateLimiter
from scanner.http.reauthentication import ReauthenticationGuard
from scanner.http.users_routes import ROUTERS
from scanner.persistence.rows.db_audit import AuditEventRow
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import MEMBER_PASSWORD, OWNER, OWNER_PASSWORD, live_accounts


def password_of(user_id: int) -> str:
    return OWNER_PASSWORD if user_id == OWNER else MEMBER_PASSWORD


def fresh(user_id: int) -> dict:
    return {"reauthentication": {"password": password_of(user_id)}}


def code(response) -> tuple[int, str | None]:
    body = response.json()
    return response.status_code, body.get("code") if isinstance(body, dict) else None


def png(width: int = 32, height: int = 16, color=(200, 30, 30, 255)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (width, height), color).save(output, format="PNG")
    return output.getvalue()


@dataclass
class UsersApp:
    client: object
    accounts: object

    def send(self, method: str, path: str, user: int | None, body=None, *, key=False, **kwargs):
        if user is None:
            headers = {"Origin": "https://editarr.example"}
        elif key:
            headers = self.accounts.key(user)
        elif method == "GET":
            headers = self.accounts.cookie(user)
        else:
            headers = self.accounts.unsafe_cookie(user)
        headers = {**headers, **kwargs.pop("headers", {})}
        self.client.cookies.clear()
        if body is not None:
            kwargs["json"] = body
        return self.client.request(method, path, headers=headers, **kwargs)

    def ok(self, method: str, path: str, user: int, body=None, status: int = 200) -> dict:
        response = self.send(method, path, user, body)
        assert response.status_code == status, response.text
        return response.json()

    def link(self, user_id: int) -> None:
        """Give an account its verified stand-in Jellyfin identity."""
        self.accounts.link(user_id)

    def unlink(self, user_id: int) -> None:
        """Remove the account's Jellyfin identity, as a fixture-only state change."""
        self.accounts.unlink(user_id)

    def audit(self) -> list[tuple]:
        with self.accounts.factory() as db:
            rows = db.execute(
                select(
                    AuditEventRow.action,
                    AuditEventRow.outcome,
                    AuditEventRow.actor_id,
                    AuditEventRow.target_user_id,
                    AuditEventRow.details_json,
                ).order_by(AuditEventRow.occurred_at)
            ).all()
            return [tuple(row) for row in rows]

    def scalar(self, statement):
        with self.accounts.factory() as db:
            return db.scalar(statement)


@pytest.fixture
def accounts(tmp_path):
    live = live_accounts(tmp_path)
    yield live
    live.close()


def _users_app(monkeypatch, accounts):
    guard = ReauthenticationGuard(LoginRateLimiter(configuration=accounts.configuration))
    monkeypatch.setattr(users_access, "reauthentication_guard", guard)
    monkeypatch.setattr(account_operations, "reauthentication_guard", guard)
    client = serve_access_fixture(monkeypatch, accounts)
    for router in ROUTERS:
        client.app.include_router(router)
    return client


@pytest.fixture
def users_app(monkeypatch, accounts):
    client = _users_app(monkeypatch, accounts)
    yield UsersApp(client, accounts)
    client.close()
