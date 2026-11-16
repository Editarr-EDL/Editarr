"""MU-15: local media defaults and individual access over HTTP."""

from sqlalchemy import select

from scanner.persistence.rows.db_auth import UserRow
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.vocabulary import Role
from tests.multi_user_access_support import ADMIN, OWNER, PEER_ADMIN, PEER_USER, USER
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import code, fresh
from tests.users_api_http_support import users_app as users_app

MOVIES = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
MOVIES_ONLY = {"sources": [{"kind": "jellyfin_library", "id": MOVIES}]}


def _revision(app, user_id):
    return app.scalar(select(UserRow.permission_revision).where(UserRow.id == user_id))


def test_default_scope_changes_need_a_fresh_password_and_reject_api_keys(users_app):
    assert users_app.ok("GET", "/api/media-access/default", ADMIN) == {
        "inherited": True,
        "all_sources": True,
        "sources": [],
    }
    assert code(users_app.send("GET", "/api/media-access/default", USER)) == (
        403,
        "capability_denied",
    )
    path = "/api/media-access/default"
    assert code(users_app.send("PUT", path, ADMIN, MOVIES_ONLY, key=True)) == (
        403,
        "interactive_session_required",
    )
    assert code(users_app.send("PUT", path, ADMIN, MOVIES_ONLY)) == (
        403,
        "reauthentication_required",
    )
    stale = {**MOVIES_ONLY, "reauthentication": {"password": "an old password value"}}
    assert code(users_app.send("PUT", path, ADMIN, stale)) == (403, "reauthentication_failed")
    assert code(users_app.send("PUT", path, USER, {**MOVIES_ONLY, **fresh(USER)})) == (
        403,
        "capability_denied",
    )
    before = (_revision(users_app, USER), _revision(users_app, ADMIN), _revision(users_app, OWNER))
    changed = users_app.ok("PUT", path, ADMIN, {**MOVIES_ONLY, **fresh(ADMIN)})
    assert changed == {
        "inherited": True,
        "all_sources": False,
        "sources": [{"kind": "jellyfin_library", "id": MOVIES}],
    }
    after = (_revision(users_app, USER), _revision(users_app, ADMIN), _revision(users_app, OWNER))
    assert after[0] == before[0] + 1 and after[2] == before[2]
    inherit = {"inherit": True, **fresh(ADMIN)}
    assert code(users_app.send("PUT", path, ADMIN, inherit)) == (422, "invalid_media_scope")


def test_admins_manage_ordinary_local_access_only_while_permitted(users_app):
    path = f"/api/users/{USER}/media-access"
    stored = users_app.ok("PUT", path, ADMIN, {**MOVIES_ONLY, **fresh(ADMIN)})
    assert stored == {
        "linked": False,
        "scope": {
            "inherited": False,
            "all_sources": False,
            "sources": [{"kind": "jellyfin_library", "id": MOVIES}],
        },
    }
    listed = users_app.ok("GET", f"/api/users?search=member{USER}", ADMIN)["items"][0]
    assert listed["overrides"]["media_access"] is True
    reset = users_app.ok("PUT", path, ADMIN, {"inherit": True, **fresh(ADMIN)})
    assert reset["scope"]["inherited"] is True
    assert code(
        users_app.send(
            "PUT", f"/api/users/{PEER_ADMIN}/media-access", ADMIN, {**MOVIES_ONLY, **fresh(ADMIN)}
        )
    ) == (
        403,
        "protected_account",
    )
    users_app.accounts.set_role_default(Role.ADMIN, C.MEDIA_ACCESS_USER_EDIT, False)
    assert code(users_app.send("PUT", path, ADMIN, {**MOVIES_ONLY, **fresh(ADMIN)})) == (
        403,
        "capability_denied",
    )
    assert code(users_app.send("GET", path, ADMIN)) == (403, "capability_denied")
    assert (
        users_app.ok("PUT", path, OWNER, {**MOVIES_ONLY, **fresh(OWNER)})["scope"]["inherited"]
        is False
    )


def test_linked_accounts_have_no_local_scope_to_edit(users_app):
    users_app.link(PEER_USER)
    path = f"/api/users/{PEER_USER}/media-access"
    before = _revision(users_app, PEER_USER)
    assert code(users_app.send("PUT", path, ADMIN, {**MOVIES_ONLY, **fresh(ADMIN)})) == (
        409,
        "linked_account",
    )
    assert users_app.ok("GET", path, ADMIN) == {"linked": True, "scope": None}
    assert _revision(users_app, PEER_USER) == before


def test_bulk_media_access_checks_each_account(users_app):
    users_app.link(PEER_USER)
    body = {
        "action": "set_media_access",
        "media_access": MOVIES_ONLY,
        "user_ids": [USER, PEER_USER, PEER_ADMIN],
        **fresh(ADMIN),
    }
    results = users_app.ok("POST", "/api/users/bulk", ADMIN, body)["results"]
    assert [(result["outcome"], result["code"]) for result in results] == [
        ("applied", None),
        ("refused", "linked_account"),
        ("refused", "protected_account"),
    ]
    assert (
        users_app.ok("GET", f"/api/users/{USER}/media-access", ADMIN)["scope"]["inherited"] is False
    )
