"""MU-14 HTTP harness: the real filter routes in front of `live_accounts`.

Accounts come from `tests.multi_user_access_support.live_accounts`
(`mu04-access-builders-3`): owner 1, admins 2 and 3, users 4 and 5, each with a
session and an API key. The app runs in verification mode with the filter routes
declared evaluated and a reauthentication limiter on a fixed monotonic clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect

import scanner.api  # noqa: F401  # catalog_settings_api imports this facade first
import scanner.http.catalog_settings_api as catalog_settings_api
import scanner.http.filter_policy_api as filter_policy_api
from scanner.http.auth_rate_limit import LoginRateLimiter
from scanner.http.reauthentication import ReauthenticationGuard
from scanner.models import McfCategory
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import MEMBER_PASSWORD, OWNER, OWNER_PASSWORD, live_accounts

BASE = "/api/settings/filters"


def rule(disposition: str, **clauses) -> dict:
    return {"id": str(uuid4()), "disposition": disposition, **clauses}


def severities(**chosen) -> list[dict]:
    return [
        {"subcategory": item.value, "severity": chosen.get(item.value, "medium")}
        for item in McfCategory
    ]


def password_of(user_id: int) -> str:
    return OWNER_PASSWORD if user_id == OWNER else MEMBER_PASSWORD


def code(response) -> tuple[int, str]:
    body = response.json()
    detail = body.get("detail")
    found = body.get("code") or (detail.get("code") if isinstance(detail, dict) else None)
    return response.status_code, found


@dataclass
class FilterApp:
    client: object
    accounts: object

    def send(self, method: str, path: str, user: int, body=None, *, key: bool = False):
        if key:
            headers = self.accounts.key(user)
        elif method == "GET":
            headers = self.accounts.cookie(user)
        else:
            headers = self.accounts.unsafe_cookie(user)
        self.client.cookies.clear()
        return self.client.request(method, path, headers=headers, json=body)

    def ok(self, method: str, path: str, user: int, body=None) -> dict:
        response = self.send(method, path, user, body)
        assert response.status_code == 200, response.text
        return response.json()

    def instance(self) -> dict:
        return self.ok("GET", BASE, OWNER)

    def set_instance(self, rules: list[dict], *, password: bool = True, **chosen) -> dict:
        """Replace the instance defaults as the owner, with the password unless told not to."""
        body = {"revision": self.instance()["revision"], "selection_rules": rules}
        body["severity_defaults"] = severities(**chosen)
        if password:
            body["reauthentication"] = {"password": OWNER_PASSWORD}
        return self.ok("PUT", BASE, OWNER, body)

    @staticmethod
    def path(of: int | None, suffix: str = "") -> str:
        return f"{BASE}/personal{suffix}" if of is None else f"{BASE}/users/{of}{suffix}"

    def view(self, user: int, of: int | None = None) -> dict:
        return self.ok("GET", self.path(of), user)

    def save(self, user: int, body: dict, of: int | None = None):
        return self.send("PUT", self.path(of), user, body)

    def live(self, user: int, of: int | None = None, **values):
        revision = self.view(user, of)["revision"]
        return self.save(user, {"mode": "live", "revision": revision, **values}, of)

    def mode(self, user: int, mode: str | None, of: int | None = None):
        revision = (self.view(user) if of is None else self.view(OWNER, of))["revision"]
        return self.send("PUT", self.path(of, "/mode"), user, {"revision": revision, "mode": mode})

    def enforce(self, user: int, target: int, rules=(), severities=(), *, password=True, key=False):
        revision = self.view(OWNER, target)["policy_revision"]
        body = {"policy_revision": revision, "rules": list(rules), "severities": list(severities)}
        if password:
            body["reauthentication"] = {"password": password_of(user)}
        return self.send("PUT", f"{BASE}/users/{target}/policy", user, body, key=key)

    def tables(self) -> dict:
        """Every row of every table, for proving reads change nothing."""
        with self.accounts.factory() as db:
            names = inspect(db.get_bind()).get_table_names()
            return {
                name: sorted(map(tuple, db.connection().exec_driver_sql(f"SELECT * FROM {name}")))
                for name in names
                if name != "auth_sessions"
            }


@pytest.fixture
def accounts(tmp_path):
    live = live_accounts(tmp_path)
    yield live
    live.close()


@pytest.fixture
def filter_app(monkeypatch, accounts):
    guard = ReauthenticationGuard(LoginRateLimiter(configuration=accounts.configuration))
    monkeypatch.setattr(filter_policy_api, "reauthentication_guard", guard)
    client = serve_access_fixture(monkeypatch, accounts)
    client.app.include_router(filter_policy_api.router)
    client.app.include_router(catalog_settings_api.router)
    yield FilterApp(client, accounts)
    client.close()


def takes_write_lock(statement, parameters) -> bool:
    """The last statement a filter writer issues before it starts deciding."""
    return statement == "BEGIN IMMEDIATE"


@pytest.fixture
def before_statement(accounts):
    """Run `interleave` before the next `times` statements that `matches` accepts.

    Statements issued by the interleaving itself never trigger it again.
    """
    hooks = []

    def install(matches, interleave, *, times=1) -> dict:
        state = {"left": times, "busy": False}

        def hook(connection, cursor, statement, parameters, context, executemany):
            if state["busy"] or not state["left"] or not matches(statement, parameters):
                return
            state["left"] -= 1
            state["busy"] = True
            try:
                interleave()
            finally:
                state["busy"] = False

        event.listen(accounts.engine, "before_cursor_execute", hook)
        hooks.append(hook)
        return state

    yield install
    for hook in hooks:
        event.remove(accounts.engine, "before_cursor_execute", hook)
