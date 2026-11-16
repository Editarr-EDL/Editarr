"""MU-15: account status, sessions, and bulk requests."""

import pytest
from sqlalchemy import func, select

from scanner.persistence.rows.db_auth import ApiKeyRow, AuthSessionRow, UserRow
from tests.multi_user_access_support import (
    ADMIN,
    JELLYFIN_DOWN,
    OWNER,
    PEER_ADMIN,
    PEER_USER,
    USER,
)
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import code, fresh
from tests.users_api_http_support import users_app as users_app


def _live_sessions(app, user_id):
    return app.scalar(
        select(func.count())
        .select_from(AuthSessionRow)
        .where(AuthSessionRow.user_id == user_id, AuthSessionRow.revoked_at.is_(None))
    )


def _column(app, column) -> dict:
    with app.accounts.factory() as db:
        return dict(db.execute(select(UserRow.id, column)).all())


@pytest.mark.parametrize("target", [PEER_ADMIN, OWNER])
def test_admins_cannot_disable_or_end_sessions_of_admins_or_owner(users_app, target):
    sessions = _live_sessions(users_app, target)
    for method, path, body in (
        ("PUT", f"/api/users/{target}/status", {"status": "disabled"}),
        ("DELETE", f"/api/users/{target}/sessions", None),
    ):
        assert code(users_app.send(method, path, ADMIN, body)) == (403, "protected_account"), path
    assert _live_sessions(users_app, target) == sessions >= 1
    assert users_app.scalar(select(UserRow.status).where(UserRow.id == target)) == "active"


def test_disable_ends_sessions_keeps_data_and_enable_restores_access(users_app):
    users_app.link(USER)
    disabled = users_app.ok("PUT", f"/api/users/{USER}/status", ADMIN, {"status": "disabled"})
    assert disabled["status"] == "disabled" and _live_sessions(users_app, USER) == 0
    assert code(users_app.send("GET", "/api/auth/api-key", USER, key=True)) == (
        403,
        "account_disabled",
    )
    assert (
        users_app.scalar(
            select(func.count()).select_from(ApiKeyRow).where(ApiKeyRow.user_id == USER)
        )
        == 1
    )
    enabled = users_app.ok("PUT", f"/api/users/{USER}/status", ADMIN, {"status": "active"})
    assert enabled["status"] == "active"
    assert users_app.send("GET", "/api/auth/api-key", USER, key=True).status_code == 200
    assert code(
        users_app.send("PUT", f"/api/users/{ADMIN}/status", ADMIN, {"status": "disabled"})
    ) == (
        409,
        "own_account",
    )
    assert code(
        users_app.send("PUT", f"/api/users/{ADMIN}/status", PEER_USER, {"status": "disabled"})
    ) == (
        404,
        "not_found",
    )


def test_an_account_without_a_jellyfin_link_stays_disabled(users_app):
    users_app.ok("PUT", f"/api/users/{USER}/status", ADMIN, {"status": "disabled"})
    users_app.ok("PUT", f"/api/users/{PEER_USER}/status", ADMIN, {"status": "disabled"})
    users_app.link(PEER_USER)

    single = users_app.send("PUT", f"/api/users/{USER}/status", ADMIN, {"status": "active"})
    bulk = users_app.ok(
        "POST", "/api/users/bulk", ADMIN, {"action": "enable", "user_ids": [USER, PEER_USER]}
    )

    assert code(single) == (409, "account_link_required")
    assert bulk["results"] == [
        {"user_id": USER, "outcome": "refused", "code": "account_link_required"},
        {"user_id": PEER_USER, "outcome": "applied", "code": None},
    ]
    statuses = _column(users_app, UserRow.status)
    assert (statuses[USER], statuses[PEER_USER]) == ("disabled", "active")
    detail = users_app.ok("GET", f"/api/users/{USER}", ADMIN)
    assert (detail["status"], detail["disable_cause"]) == ("disabled", "local")


def test_admin_ends_an_ordinary_users_sessions_only(users_app):
    response = users_app.ok("DELETE", f"/api/users/{USER}/sessions", ADMIN)
    assert response == {"sessions_revoked": 1}
    assert _live_sessions(users_app, USER) == 0 and _live_sessions(users_app, PEER_USER) == 1
    assert users_app.ok("DELETE", f"/api/users/{USER}/sessions", ADMIN) == {"sessions_revoked": 0}
    rows = [row for row in users_app.audit() if row[0] == "sessions.revoke"]
    assert [(row[1], row[2], row[3]) for row in rows] == [
        ("succeeded", ADMIN, USER),
        ("succeeded", ADMIN, USER),
    ]


def test_bulk_checks_every_target_and_skips_protected_ones(users_app):
    body = {"action": "disable", "user_ids": [USER, PEER_ADMIN, OWNER, ADMIN, 999, PEER_USER]}
    results = users_app.ok("POST", "/api/users/bulk", ADMIN, body)["results"]
    assert results == [
        {"user_id": USER, "outcome": "applied", "code": None},
        {"user_id": PEER_ADMIN, "outcome": "refused", "code": "protected_account"},
        {"user_id": OWNER, "outcome": "refused", "code": "protected_account"},
        {"user_id": ADMIN, "outcome": "refused", "code": "own_account"},
        {"user_id": 999, "outcome": "refused", "code": "not_found"},
        {"user_id": PEER_USER, "outcome": "applied", "code": None},
    ]
    statuses = _column(users_app, UserRow.status)
    assert statuses == {
        OWNER: "active",
        ADMIN: "active",
        PEER_ADMIN: "active",
        USER: "disabled",
        PEER_USER: "disabled",
    }


def test_bulk_role_changes_stay_owner_only_with_one_password_check(users_app):
    body = {"action": "set_role", "role": "admin", "user_ids": [USER, PEER_USER]}
    refused = users_app.ok("POST", "/api/users/bulk", ADMIN, {**body, **fresh(ADMIN)})["results"]
    assert {result["code"] for result in refused} == {"capability_denied"}
    by_key = users_app.send("POST", "/api/users/bulk", OWNER, body, key=True).json()["results"]
    assert {result["code"] for result in by_key} == {"interactive_session_required"}
    missing = users_app.ok("POST", "/api/users/bulk", OWNER, body)["results"]
    assert {result["code"] for result in missing} == {"reauthentication_required"}
    wrong = {**body, "reauthentication": {"password": "wrong owner password"}}
    assert code(users_app.send("POST", "/api/users/bulk", OWNER, wrong)) == (
        403,
        "reauthentication_failed",
    )
    applied = users_app.ok("POST", "/api/users/bulk", OWNER, {**body, **fresh(OWNER)})["results"]
    assert [result["outcome"] for result in applied] == ["applied", "applied"]
    roles = _column(users_app, UserRow.role)
    assert (roles[USER], roles[PEER_USER]) == ("admin", "admin")


def test_a_jellyfin_outage_stops_a_bulk_change_before_any_account(users_app):
    body = {"action": "set_role", "role": "admin", "user_ids": [USER, PEER_USER]}
    outage = {**body, "reauthentication": {"password": JELLYFIN_DOWN}}
    for _ in range(users_app.accounts.configuration.auth_login_username_attempts + 1):
        assert code(users_app.send("POST", "/api/users/bulk", OWNER, outage)) == (
            503,
            "jellyfin_unavailable",
        )
    roles = _column(users_app, UserRow.role)
    assert (roles[USER], roles[PEER_USER]) == ("user", "user")
    applied = users_app.ok("POST", "/api/users/bulk", OWNER, {**body, **fresh(OWNER)})["results"]
    assert [result["outcome"] for result in applied] == ["applied", "applied"]


def test_bulk_requests_are_validated(users_app):
    for body in (
        {"action": "disable", "user_ids": []},
        {"action": "disable", "user_ids": [USER, USER]},
        {"action": "set_role", "user_ids": [USER]},
        {"action": "disable", "role": "admin", "user_ids": [USER]},
        {"action": "delete", "user_ids": [USER]},
    ):
        assert users_app.send("POST", "/api/users/bulk", OWNER, body).status_code == 422, body
