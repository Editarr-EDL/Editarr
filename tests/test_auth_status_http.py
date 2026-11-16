"""MU-19 status contract: the session's own access, name, and sign-in options."""

from __future__ import annotations

from scanner.db import JellyfinSettingsRow
from tests.multi_user_access_support import ADMIN, OWNER, USER
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import fresh as fresh
from tests.users_api_http_support import users_app as users_app


def _status(app, user):
    return app.ok("GET", "/api/auth/status", user)


def test_the_status_carries_the_evaluator_payload_for_each_session(users_app):
    for user in (OWNER, ADMIN, USER):
        body = _status(users_app, user)
        assert "configuration" not in body
        assert body["access"] == users_app.ok("GET", "/api/test-access/payload", user)
        assert body["access"]["principal"]["user_id"] == user
        assert body["access"]["principal"]["credential"] == "session"


def test_a_signed_out_status_names_no_account_and_offers_only_sign_in(users_app):
    body = users_app.send("GET", "/api/auth/status", None).json()

    assert body["state"] == "unauthenticated"
    assert (body["display_name"], body["access"], body["csrf_token"]) == (None, None, None)
    assert body["sign_in"] == {"jellyfin": False}


def test_a_role_change_raises_the_revision_and_changes_the_capabilities(users_app):
    before = _status(users_app, USER)["access"]
    users_app.ok("PUT", f"/api/users/{USER}/role", OWNER, {"role": "admin", **fresh(OWNER)})
    after = _status(users_app, USER)["access"]

    assert after["permission_revision"] > before["permission_revision"]
    assert after["principal"]["role"] == "admin"
    assert "accounts.list" in after["capabilities"]
    assert "accounts.list" not in before["capabilities"]


def test_the_display_name_is_the_profile_name_else_the_username(users_app):
    assert _status(users_app, USER)["display_name"] == f"member{USER}"
    users_app.ok("PATCH", f"/api/users/{USER}", USER, {"display_name": "Member Four"})

    assert _status(users_app, USER)["display_name"] == "Member Four"


def test_jellyfin_sign_in_is_offered_while_a_connection_is_configured(users_app):
    with users_app.accounts.factory() as db:
        db.add(JellyfinSettingsRow(id=1, server_url="http://127.0.0.1:9", api_key="k" * 32))
        db.commit()

    signed_out = users_app.send("GET", "/api/auth/status", None).json()
    assert signed_out["sign_in"]["jellyfin"] is True
    assert _status(users_app, OWNER)["sign_in"]["jellyfin"] is True
