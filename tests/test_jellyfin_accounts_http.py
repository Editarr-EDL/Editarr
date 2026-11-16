"""MU-16 HTTP tests: Jellyfin sign-in, import, manual sync, admission, and account effects.

Real routers and authentication middleware over `live_accounts`
(`mu04-access-builders-3`: owner 1, admins 2 and 3, users 4 and 5). Jellyfin is
the MU-08 `JellyfinBoundary` over local HTTP (`editarr-jellyfin-users-1`), not a
live server. Session, verification, and grace times share one fixed clock.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

import scanner.http.jellyfin_accounts_api as jellyfin_accounts_api
import scanner.http.jellyfin_login_api as jellyfin_login_api
from scanner.db import (
    ApiKeyRow,
    JellyfinSettingsRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.http.auth_rate_limit import LoginRateLimiter
from scanner.persistence.rows.db_auth import AccountSettingsRow
from scanner.security import auth_policy
from scanner.security.access import link_state
from scanner.security.access.link_state import VERIFICATION_GRACE
from scanner.security.api_key_crypto import load_api_key_wrap_key
from scanner.security.api_keys import rotate_api_key
from scanner.security.jellyfin_accounts.sync import SyncRegistry
from tests.jellyfin_user_boundary import IDS, JellyfinBoundary
from tests.multi_user_access_support import ADMIN, ORIGIN, OWNER, USER, live_accounts
from tests.users_api_http_support import UsersApp, _users_app, fresh

PASSWORDS = {user_id: f"jf-{name.lower()}-password" for name, user_id in IDS.items()}


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fixed = Clock()
    monkeypatch.setattr(link_state, "link_clock", fixed)
    monkeypatch.setattr(auth_policy, "session_now", fixed)
    return fixed


@pytest.fixture
def boundary():
    with JellyfinBoundary(secrets.token_urlsafe(24), PASSWORDS) as server:
        yield server


def _app(monkeypatch, tmp_path, boundary):
    accounts = live_accounts(tmp_path)
    with accounts.factory() as db:
        db.add(JellyfinSettingsRow(id=1, server_url=boundary.url, api_key=boundary.api_key))
        db.commit()
    monkeypatch.setattr(jellyfin_accounts_api, "registry", SyncRegistry())
    monkeypatch.setattr(jellyfin_login_api, "jellyfin_login_limiter", LoginRateLimiter())
    client = _users_app(monkeypatch, accounts)
    client.app.include_router(jellyfin_login_api.router)
    client.app.include_router(jellyfin_accounts_api.router)
    return UsersApp(client, accounts)


@pytest.fixture
def app(monkeypatch, tmp_path, boundary, clock):
    users = _app(monkeypatch, tmp_path, boundary)
    yield users
    users.client.close()
    users.accounts.close()


def _sign_in(app, name, password=None, *, remember_me=False):
    app.client.cookies.clear()
    body = {
        "username": name,
        "password": PASSWORDS[IDS[name.title()]] if password is None else password,
        "remember_me": remember_me,
    }
    return app.client.post("/api/auth/jellyfin/login", json=body, headers=ORIGIN)


def _session(response):
    token = response.cookies["editarr_session"]
    return {**ORIGIN, "Cookie": f"editarr_session={token}"}, response.json()["csrf_token"]


def _linked(app, name):
    return app.scalar(
        select(UserProviderIdentityRow.user_id).where(
            UserProviderIdentityRow.provider_user_id == IDS[name]
        )
    )


def _key(app, user_id):
    with app.accounts.factory() as db:
        key = rotate_api_key(
            db, user_id, load_api_key_wrap_key(app.accounts.configuration.data_dir)
        )
    return {"X-Api-Key": key}


def _sync(app):
    assert app.send("POST", "/api/jellyfin/sync", OWNER, key=True).status_code == 200


def _get(app, path, headers):
    app.client.cookies.clear()
    return app.client.get(path, headers=headers)


def test_first_sign_in_creates_an_account_and_issues_a_session(app):
    response = _sign_in(app, "Casey", remember_me=True)

    assert response.status_code == 200 and response.json()["state"] == "authenticated"
    casey = _linked(app, "Casey")
    signed_in = response.json()
    assert signed_in["display_name"] == "Casey"
    assert signed_in["access"]["principal"]["user_id"] == casey
    assert signed_in["access"]["principal"]["link_state"] == "verified"
    headers, _ = _session(response)
    status = _get(app, "/api/auth/status", headers).json()
    assert status["state"] == "authenticated"
    assert status["access"] == signed_in["access"]
    profile = _get(app, f"/api/users/{casey}", headers).json()
    assert (profile["display_name"], profile["source"], profile["role"]) == (
        "Casey",
        "jellyfin",
        "user",
    )
    assert _sign_in(app, "casey").status_code == 200
    assert (
        app.scalar(
            select(func.count())
            .select_from(UserProviderIdentityRow)
            .where(UserProviderIdentityRow.user_id != OWNER)
        )
        == 1
    )


@pytest.mark.parametrize(
    ("name", "password", "code"),
    [
        ("Casey", "wrong", (401, "invalid_credentials")),
        ("Cas\ud800ey", "x", (401, "invalid_credentials")),
        ("Casey", "p\udfff", (401, "invalid_credentials")),
        ("C" * 257, "x", (401, "invalid_credentials")),
    ],
)
def test_bad_credentials_never_create_an_account(app, boundary, name, password, code):
    sent = len(boundary.requests)
    body = json.dumps({"username": name, "password": password}, ensure_ascii=True)
    response = app.client.post(
        "/api/auth/jellyfin/login",
        content=body.encode(),
        headers={**ORIGIN, "Content-Type": "application/json"},
    )

    assert (response.status_code, response.json()["code"]) == code
    assert (
        app.scalar(
            select(func.count())
            .select_from(UserProviderIdentityRow)
            .where(UserProviderIdentityRow.user_id != OWNER)
        )
        == 0
    )
    if name != "Casey":
        assert len(boundary.requests) == sent


def test_unencodable_credentials_are_refused_before_a_jellyfin_read_starts(app):
    generation = select(AccountSettingsRow.jellyfin_read_generation)
    before = app.scalar(generation)
    body = json.dumps({"username": "Casey", "password": "p\udfff"}, ensure_ascii=True)
    response = app.client.post(
        "/api/auth/jellyfin/login",
        content=body.encode(),
        headers={**ORIGIN, "Content-Type": "application/json"},
    )

    assert (response.status_code, response.json()["code"]) == (401, "invalid_credentials")
    assert app.scalar(generation) == before
    assert ("auth.sign_in", "failed") in [row[:2] for row in app.audit()]


def test_admission_has_no_setting_and_every_new_user_is_admitted(app):
    for method in ("GET", "PUT"):
        missing = app.send(method, "/api/settings/jellyfin-admission", OWNER, {"mode": "x"})
        assert missing.status_code in (404, 405)

    assert _sign_in(app, "Casey").status_code == 200
    assert _sign_in(app, "Avery").status_code == 200
    roles = {
        name: app.scalar(select(UserRow.role).where(UserRow.id == _linked(app, name)))
        for name in ("Casey", "Avery")
    }
    assert roles == {"Casey": "user", "Avery": "admin"}


def test_admins_import_and_users_cannot(app):
    listing = app.ok("GET", "/api/jellyfin/users", ADMIN)["users"]
    assert {user["name"] for user in listing} == {"Avery", "Blake", "Casey", "Drew", "Emery"}
    assert all(user["user_id"] is None for user in listing)
    assert app.send("GET", "/api/jellyfin/users", USER).status_code == 403
    bad = app.send(
        "POST", "/api/jellyfin/users/import", ADMIN, {"all": True, "user_ids": [IDS["Avery"]]}
    )
    assert bad.status_code == 422

    imported = app.ok("POST", "/api/jellyfin/users/import", ADMIN, {"all": True})["results"]

    assert {result["name"]: result["outcome"] for result in imported} == dict.fromkeys(
        ("Avery", "Blake", "Casey", "Drew", "Emery"), "imported"
    )
    again = app.ok("POST", "/api/jellyfin/users/import", OWNER, {"user_ids": [IDS["Avery"]]})
    assert again["results"][0]["outcome"] == "existing"
    assert app.ok("GET", f"/api/users/{_linked(app, 'Avery')}", OWNER)["role"] == "admin"


def test_roles_start_from_jellyfin_and_then_stay_as_the_owner_sets_them(app, boundary):
    app.ok("POST", "/api/jellyfin/users/import", ADMIN, {"all": True})
    avery, blake = _linked(app, "Avery"), _linked(app, "Blake")
    app.ok("PUT", f"/api/users/{avery}/role", OWNER, {"role": "user", **fresh(OWNER)})

    boundary.state = "changed"
    _sync(app)
    assert app.ok("GET", f"/api/users/{blake}", OWNER)["role"] == "user"

    boundary.state = "baseline"
    _sync(app)
    assert _sign_in(app, "Avery").status_code == 200
    roles = {
        user_id: app.ok("GET", f"/api/users/{user_id}", OWNER)["role"] for user_id in (avery, blake)
    }
    assert roles == {avery: "user", blake: "user"}


def test_upstream_disable_and_delete_block_sessions_and_keys_and_keep_them(app, boundary):
    app.ok("POST", "/api/jellyfin/users/import", ADMIN, {"all": True})
    drew, emery = _linked(app, "Drew"), _linked(app, "Emery")
    sessions = {name: _session(_sign_in(app, name))[0] for name in ("Drew", "Emery")}
    keys = {drew: _key(app, drew), emery: _key(app, emery)}
    assert _get(app, f"/api/users/{drew}", keys[drew]).status_code == 200
    boundary.state = "changed"

    run = app.ok("POST", "/api/jellyfin/sync", ADMIN)

    outcomes = {result["user_id"]: result["outcome"] for result in run["results"]}
    assert (outcomes[drew], outcomes[emery]) == ("disabled", "deleted")
    for user_id, name in ((drew, "Drew"), (emery, "Emery")):
        assert _get(app, f"/api/users/{user_id}", sessions[name]).status_code == 401
        denied = _get(app, f"/api/users/{user_id}", keys[user_id])
        assert (denied.status_code, denied.json()["code"]) == (403, "account_disabled")
        assert app.scalar(select(ApiKeyRow.user_id).where(ApiKeyRow.user_id == user_id))
    assert app.ok("GET", f"/api/users/{drew}", OWNER)["status"] == "disabled"


def test_jellyfin_re_enabling_a_user_restores_the_account_but_not_its_sessions(app, boundary):
    app.ok("POST", "/api/jellyfin/users/import", ADMIN, {"all": True})
    drew = _linked(app, "Drew")
    earlier = _session(_sign_in(app, "Drew"))[0]
    key = _key(app, drew)
    boundary.state = "changed"
    _sync(app)
    boundary.state = "baseline"

    _sync(app)

    assert app.ok("GET", f"/api/users/{drew}", OWNER)["status"] == "active"
    assert _get(app, f"/api/users/{drew}", earlier).status_code == 401
    assert _get(app, f"/api/users/{drew}", key).status_code == 200
    assert _sign_in(app, "Drew").status_code == 200


def test_a_local_disable_outlasts_jellyfin_and_a_local_enable_yields_to_it(app, boundary):
    app.ok("POST", "/api/jellyfin/users/import", ADMIN, {"all": True})
    drew = _linked(app, "Drew")
    boundary.state = "changed"
    _sync(app)
    app.ok("PUT", f"/api/users/{drew}/status", ADMIN, {"status": "active"})
    assert _get(app, f"/api/users/{drew}", _key(app, drew)).status_code == 200

    _sync(app)
    assert app.ok("GET", f"/api/users/{drew}", OWNER)["status"] == "disabled"

    app.ok("PUT", f"/api/users/{drew}/status", ADMIN, {"status": "disabled"})
    boundary.state = "baseline"
    _sync(app)
    assert app.ok("GET", f"/api/users/{drew}", OWNER)["status"] == "disabled"
    refused = _sign_in(app, "Drew")
    assert (refused.status_code, refused.json()["code"]) == (403, "account_disabled")


def test_manual_sync_is_for_admins_and_reports_status(app, boundary, clock):
    app.ok("POST", "/api/jellyfin/users/import", ADMIN, {"user_ids": [IDS["Blake"]]})
    assert app.send("POST", "/api/jellyfin/sync", USER).status_code == 403
    assert app.send("GET", "/api/jellyfin/sync", USER).status_code == 403
    by_key = app.send("POST", "/api/jellyfin/sync", ADMIN, key=True)
    assert by_key.status_code == 200
    jellyfin_accounts_api.registry.lock.acquire()
    busy = app.send("POST", "/api/jellyfin/sync", OWNER)
    jellyfin_accounts_api.registry.lock.release()
    assert (busy.status_code, busy.json()["code"]) == (409, "sync_in_progress")

    boundary.fault = "server-error"
    clock.now += timedelta(hours=2)
    assert app.send("POST", "/api/jellyfin/sync", OWNER, key=True).status_code == 200
    status = app.send("GET", "/api/jellyfin/sync", ADMIN, key=True).json()

    # The owner's link is to the `live_accounts` stand-in server.
    [account] = [entry for entry in status["accounts"] if entry["user_id"] != OWNER]
    verified = datetime.fromisoformat(account["last_verified_at"])
    assert account["link_state"] == "outage_grace"
    assert (account["last_outcome"], account["last_failure"]) == ("unverified", "service_error")
    assert datetime.fromisoformat(account["grace_expires_at"]) == verified + VERIFICATION_GRACE
    # Blake and the owner, whose read the same server error stopped.
    assert status["last_run"]["counts"]["unverified"] == 2 and status["running"] is False


def test_outage_expiry_and_replacement_keep_profile_access_only(app, boundary, clock):
    app.ok("POST", "/api/jellyfin/users/import", ADMIN, {"user_ids": [IDS["Avery"]]})
    avery = _linked(app, "Avery")
    key = _key(app, avery)
    session = _session(_sign_in(app, "Avery", remember_me=True))[0]
    assert _get(app, "/api/users", key).status_code == 200
    boundary.fault = "server-error"
    clock.now += VERIFICATION_GRACE
    _sync(app)
    assert _get(app, "/api/users", key).status_code == 200

    clock.now += timedelta(seconds=1)
    denied = _get(app, "/api/users", key)
    assert (denied.status_code, denied.json()["code"]) == (403, "capability_denied")
    assert _get(app, f"/api/users/{avery}", key).status_code == 200

    boundary.fault = None
    _sync(app)
    assert _get(app, "/api/users", key).status_code == 200
    boundary.state = "replaced"
    _sync(app)
    assert _get(app, "/api/users", key).status_code == 403
    assert _get(app, f"/api/users/{avery}", key).status_code == 200
    assert _get(app, "/api/users", session).status_code == 403
    assert _get(app, f"/api/users/{avery}", session).status_code == 200


def test_a_linked_account_reauthenticates_with_its_jellyfin_identity(app, boundary):
    casey = _session(_sign_in(app, "Casey"))

    def issue(session, password):
        app.client.cookies.clear()
        headers, csrf = session
        return app.client.post(
            "/api/auth/api-key",
            json={"reauthentication": {"password": password}},
            headers={**headers, "X-CSRF-Token": csrf},
        )

    wrong = issue(casey, "not the password")
    assert (wrong.status_code, wrong.json()["code"]) == (403, "reauthentication_failed")
    assert issue(casey, PASSWORDS[IDS["Casey"]]).status_code == 200
    # The fixture renames Blake's Jellyfin user to "Casey". Blake's password now
    # signs in as "Casey", but proves a different Jellyfin user than this account's.
    boundary.state = "changed"
    renamed = issue(casey, PASSWORDS[IDS["Blake"]])
    assert (renamed.status_code, renamed.json()["code"]) == (403, "reauthentication_failed")


def test_a_jellyfin_owner_confirms_every_sensitive_action_with_its_jellyfin_password(app):
    """Ownership moves to linked accounts with no local password and back between them."""

    for name in ("Avery", "Blake"):
        assert _sign_in(app, name).status_code == 200
    avery, blake = _linked(app, "Avery"), _linked(app, "Blake")
    assert app.send("POST", f"/api/users/{avery}/ownership", OWNER, fresh(OWNER)).status_code == 200

    def confirmed(name, method, path, body=None, password=None):
        headers, csrf = _session(_sign_in(app, name))
        app.client.cookies.clear()
        proof = {"password": PASSWORDS[IDS[name]] if password is None else password}
        return app.client.request(
            method,
            path,
            json={**(body or {}), "reauthentication": proof},
            headers={**headers, "X-CSRF-Token": csrf},
        )

    def status(response):
        return response.status_code, response.json().get("code")

    wrong = confirmed("Avery", "POST", "/api/auth/api-key", password="not the password")
    assert status(wrong) == (403, "reauthentication_failed")
    results = {
        "api key": confirmed("Avery", "POST", "/api/auth/api-key"),
        "role": confirmed("Avery", "PUT", f"/api/users/{USER}/role", {"role": "admin"}),
        "to Blake": confirmed("Avery", "POST", f"/api/users/{blake}/ownership"),
        "back to Avery": confirmed("Blake", "POST", f"/api/users/{avery}/ownership"),
    }
    assert {action: response.status_code for action, response in results.items()} == dict.fromkeys(
        results, 200
    )
    headers, _ = _session(_sign_in(app, "Avery"))
    preview = _get(app, f"/api/users/{USER}/deletion?mode=retain", headers)
    assert preview.status_code == 200, preview.text
    deleted = confirmed(
        "Avery",
        "POST",
        f"/api/users/{USER}/deletion",
        {"mode": "retain", "confirmation": preview.json()["confirmation"], "files": []},
    )
    assert deleted.status_code == 200, deleted.text
    with app.accounts.factory() as db:
        rows = db.execute(select(UserRow.id, UserRow.role, UserRow.status)).tuples().all()
    accounts = {user_id: (role, state) for user_id, role, state in rows}
    assert [accounts[user] for user in (avery, blake, OWNER, USER)] == [
        ("owner", "active"),
        ("admin", "active"),
        ("admin", "active"),
        ("admin", "disabled"),
    ]


def test_fresh_owner_actions_still_need_the_owner_password(app):
    assert app.send("POST", "/api/auth/api-key", OWNER, fresh(OWNER)).status_code == 200
