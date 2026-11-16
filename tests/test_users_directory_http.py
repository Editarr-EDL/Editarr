"""MU-15: the Users directory, own and managed profiles, and target protection over HTTP."""

import json
import sqlite3

import pytest
from sqlalchemy import select

from scanner.persistence.rows.db_auth import UserProfileRow, UserRow
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import ADMIN, OWNER, PEER_ADMIN, PEER_USER, USER
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import code
from tests.users_api_http_support import users_app as users_app

SUMMARY_FIELDS = {
    "id",
    "username",
    "display_name",
    "source",
    "role",
    "status",
    "disable_cause",
    "last_login_at",
    "created_at",
    "avatar",
    "overrides",
}


def test_directory_pages_with_the_approved_columns_only(users_app):
    first = users_app.ok("GET", "/api/users?limit=2", ADMIN)
    assert (first["total"], first["offset"], first["limit"]) == (5, 0, 2)
    assert [item["id"] for item in first["items"]] == [OWNER, ADMIN]
    rest = users_app.ok("GET", "/api/users?offset=2&limit=50", ADMIN)
    assert [item["id"] for item in rest["items"]] == [PEER_ADMIN, USER, PEER_USER]
    for item in first["items"] + rest["items"]:
        assert set(item) == SUMMARY_FIELDS
        assert "email" not in item and "password_hash" not in json.dumps(item)
    owner = first["items"][0]
    assert (owner["role"], owner["source"], owner["status"]) == ("owner", "jellyfin", "active")
    # Every fixture account was given a session, which records its sign-in.
    assert all(item["last_login_at"] for item in first["items"] + rest["items"])
    assert owner["overrides"] == {
        "permissions": False,
        "media_access": False,
        "filter_policy": False,
    }


def test_directory_search_and_indicators(users_app):
    users_app.accounts.set_override(USER, C.SCANS_START, OverrideEffect.DENY)
    users_app.ok("PATCH", f"/api/users/{PEER_USER}", ADMIN, {"display_name": "Zed Watcher"})
    found = users_app.ok("GET", "/api/users?search=WATCH", OWNER)
    assert [item["id"] for item in found["items"]] == [PEER_USER] and found["total"] == 1
    assert users_app.ok("GET", "/api/users?search=member4", OWNER)["items"][0]["overrides"] == {
        "permissions": True,
        "media_access": False,
        "filter_policy": False,
    }
    assert users_app.ok("GET", "/api/users?search=%25", OWNER)["total"] == 0


@pytest.mark.parametrize(
    "removed",
    [
        "reviews.import.server_path",
        "media_access.exceptions.edit",
        "profile.password.change.own",
        "accounts.password.reset.other",
    ],
)
def test_an_override_for_a_removed_capability_is_not_listed(users_app, removed):
    with sqlite3.connect(users_app.accounts.path) as database:
        database.execute(
            "INSERT INTO user_capability_overrides (user_id, capability, effect, updated_at) "
            "VALUES (?, ?, 'allow', '2026-11-03')",
            (USER, removed),
        )
    listed = users_app.ok("GET", "/api/users?search=member4", OWNER)["items"][0]
    assert listed["id"] == USER and listed["overrides"]["permissions"] is False
    assert users_app.ok("GET", f"/api/users/{USER}/permissions", OWNER)["overrides"] == {}
    with sqlite3.connect(users_app.accounts.path) as database:
        stored = database.execute(
            "SELECT capability FROM user_capability_overrides WHERE user_id = ?", (USER,)
        ).fetchall()
    assert stored == [(removed,)]


@pytest.mark.parametrize("key", [False, True])
def test_ordinary_users_cannot_list_or_read_others(users_app, key):
    assert code(users_app.send("GET", "/api/users", USER, key=key)) == (403, "capability_denied")
    assert code(users_app.send("GET", "/api/users?search=owner", USER, key=key))[0] == 403
    for target in (PEER_USER, ADMIN, OWNER, 999):
        response = users_app.send("GET", f"/api/users/{target}", USER, key=key)
        assert code(response) == (403, "capability_denied"), target
        assert code(users_app.send("GET", f"/api/users/{target}/avatar", USER, key=key))[0] == 403


def test_users_read_and_edit_their_own_permitted_fields(users_app):
    own = users_app.ok("GET", f"/api/users/{USER}", USER)
    assert (own["id"], own["email"], own["available_languages"]) == (USER, None, ["en"])
    changed = users_app.ok(
        "PATCH",
        f"/api/users/{USER}",
        USER,
        {"display_name": "  Ann\u0301  ", "email": "ann@example.test", "timezone": "Europe/Paris"},
    )
    assert (changed["display_name"], changed["email"], changed["timezone"]) == (
        "Ann\u0301".replace("n\u0301", "\u0144"),
        "ann@example.test",
        "Europe/Paris",
    )
    cleared = users_app.ok("PATCH", f"/api/users/{USER}", USER, {"email": None, "language": "en"})
    assert (cleared["email"], cleared["language"], cleared["timezone"]) == (
        None,
        "en",
        "Europe/Paris",
    )
    stored = users_app.scalar(select(UserProfileRow.timezone).where(UserProfileRow.user_id == USER))
    assert stored == "Europe/Paris"
    key = users_app.send("PATCH", f"/api/users/{USER}", USER, {"timezone": "UTC"}, key=True)
    assert key.status_code == 200 and key.json()["timezone"] == "UTC"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"display_name": ""}, "invalid_display_name"),
        ({"display_name": "x" * 101}, "invalid_display_name"),
        ({"display_name": "bell\u0007"}, "invalid_display_name"),
        ({"email": "not-an-address"}, "invalid_email"),
        ({"timezone": "Mars/Olympus"}, "invalid_timezone"),
        ({"language": "fr"}, "unsupported_language"),
    ],
)
def test_profile_values_are_validated(users_app, body, expected):
    assert code(users_app.send("PATCH", f"/api/users/{USER}", USER, body)) == (422, expected)
    assert (
        users_app.scalar(select(UserProfileRow.display_name).where(UserProfileRow.user_id == USER))
        is None
    )


def test_no_account_edits_a_username(users_app):
    users_app.link(PEER_USER)
    names = select(UserRow.id, UserRow.username).order_by(UserRow.id)
    with users_app.accounts.factory() as database:
        before = database.execute(names).all()
    for actor, target in ((USER, USER), (ADMIN, USER), (PEER_USER, PEER_USER)):
        response = users_app.send("PATCH", f"/api/users/{target}", actor, {"username": "renamed"})
        assert response.status_code == 422
    with users_app.accounts.factory() as database:
        assert database.execute(names).all() == before


def test_admins_manage_ordinary_profiles_but_not_admins_or_owner(users_app):
    edited = users_app.ok("PATCH", f"/api/users/{USER}", ADMIN, {"display_name": "Edited"})
    assert edited["display_name"] == "Edited"
    for target in (PEER_ADMIN, OWNER):
        refused = users_app.send("PATCH", f"/api/users/{target}", ADMIN, {"display_name": "X"})
        assert code(refused) == (403, "protected_account")
    assert users_app.ok("GET", f"/api/users/{OWNER}", ADMIN)["display_name"] is None
    owner_edit = users_app.ok("PATCH", f"/api/users/{PEER_ADMIN}", OWNER, {"display_name": "Peer"})
    assert owner_edit["display_name"] == "Peer"
    actions = [(row[0], row[1], row[3]) for row in users_app.audit() if row[0] == "account.update"]
    assert actions == [
        ("account.update", "succeeded", USER),
        ("account.update", "denied", PEER_ADMIN),
        ("account.update", "denied", OWNER),
        ("account.update", "succeeded", PEER_ADMIN),
    ]


def test_ordinary_users_cannot_substitute_another_target(users_app):
    for target in (PEER_USER, ADMIN, OWNER):
        refused = users_app.send("PATCH", f"/api/users/{target}", USER, {"display_name": "Hijack"})
        assert code(refused) == (404, "not_found")
    assert code(users_app.send("PATCH", "/api/users/999", USER, {"display_name": "x"})) == (
        404,
        "not_found",
    )
    assert users_app.scalar(select(UserProfileRow.display_name)) is None


def test_linked_accounts_keep_jellyfin_name_but_edit_preferences(users_app):
    users_app.link(PEER_USER)
    detail = users_app.ok("GET", f"/api/users/{PEER_USER}", ADMIN)
    assert (detail["source"], detail["read_only_fields"]) == (
        "jellyfin",
        ["username", "display_name", "avatar"],
    )
    for actor, change in (
        (PEER_USER, {"display_name": "Me"}),
        (ADMIN, {"display_name": "Renamed"}),
    ):
        refused = users_app.send("PATCH", f"/api/users/{PEER_USER}", actor, change)
        assert code(refused) == (409, "linked_profile_field")
    kept = users_app.ok(
        "PATCH",
        f"/api/users/{PEER_USER}",
        PEER_USER,
        {"email": "me@example.test", "timezone": "UTC"},
    )
    assert (kept["email"], kept["timezone"]) == ("me@example.test", "UTC")
