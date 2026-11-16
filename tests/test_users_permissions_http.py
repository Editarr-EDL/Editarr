"""MU-15: owner-only roles, overrides, and role defaults over HTTP."""

import json

import pytest
from sqlalchemy import select

from scanner.persistence.rows.db_auth import UserRow
from tests.multi_user_access_support import ADMIN, OWNER, PEER_ADMIN, PEER_USER, USER
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import code, fresh
from tests.users_api_http_support import users_app as users_app


def _revision(app, user_id):
    return app.scalar(select(UserRow.permission_revision).where(UserRow.id == user_id))


def test_only_the_owner_assigns_roles_with_a_fresh_password(users_app):
    path = f"/api/users/{USER}/role"
    assert code(users_app.send("PUT", path, ADMIN, {"role": "admin", **fresh(ADMIN)})) == (
        403,
        "capability_denied",
    )
    assert code(users_app.send("PUT", path, OWNER, {"role": "admin"})) == (
        403,
        "reauthentication_required",
    )
    assert code(users_app.send("PUT", path, OWNER, {"role": "admin"}, key=True)) == (
        403,
        "interactive_session_required",
    )
    assert users_app.send("PUT", path, OWNER, {"role": "owner", **fresh(OWNER)}).status_code == 422
    before = _revision(users_app, USER)
    promoted = users_app.ok("PUT", path, OWNER, {"role": "admin", **fresh(OWNER)})
    assert promoted["role"] == "admin" and _revision(users_app, USER) == before + 1
    # The promoted account lists users on its very next request.
    assert users_app.send("GET", "/api/users", USER).status_code == 200
    users_app.ok("PUT", f"/api/users/{PEER_ADMIN}/role", OWNER, {"role": "user", **fresh(OWNER)})
    assert code(users_app.send("GET", "/api/users", PEER_ADMIN)) == (403, "capability_denied")
    assert code(
        users_app.send("PUT", f"/api/users/{OWNER}/role", OWNER, {"role": "user", **fresh(OWNER)})
    ) == (
        409,
        "own_account",
    )


def test_role_sync_is_gone(users_app):
    users_app.link(PEER_USER)
    gone = users_app.send("POST", f"/api/users/{PEER_USER}/role-sync", OWNER, fresh(OWNER))
    assert gone.status_code in (404, 405)


def test_owner_overrides_are_live_and_never_grant_owner_powers(users_app):
    path = f"/api/users/{USER}/permissions"
    assert code(users_app.send("GET", path, ADMIN)) == (403, "capability_denied")
    assert code(
        users_app.send("PATCH", path, ADMIN, {"overrides": {"scans.start": "deny"}, **fresh(ADMIN)})
    ) == (
        403,
        "capability_denied",
    )
    owner_power = {"overrides": {"roles.assign": "allow"}, **fresh(OWNER)}
    assert code(users_app.send("PATCH", path, OWNER, owner_power)) == (422, "owner_only_capability")
    denied = users_app.ok(
        "PATCH", path, OWNER, {"overrides": {"scans.start": "deny"}, **fresh(OWNER)}
    )
    assert denied["overrides"] == {"scans.start": "deny"}
    assert "scans.start" not in denied["effective"]
    granted = users_app.ok(
        "PATCH",
        path,
        OWNER,
        {"overrides": {"accounts.list": "allow", "scans.start": "inherit"}, **fresh(OWNER)},
    )
    assert (
        granted["overrides"] == {"accounts.list": "allow"} and "scans.start" in granted["effective"]
    )
    assert users_app.send("GET", "/api/users", USER).status_code == 200
    assert code(users_app.send("PATCH", f"/api/users/{OWNER}/permissions", OWNER, owner_power)) == (
        409,
        "own_account",
    )


def test_role_defaults_are_owner_only_and_apply_live(users_app):
    assert code(users_app.send("GET", "/api/roles/defaults", ADMIN)) == (403, "capability_denied")
    defaults = users_app.ok("GET", "/api/roles/defaults", OWNER)
    listing = next(row for row in defaults["admin"] if row["capability"] == "accounts.list")
    assert listing == {
        "capability": "accounts.list",
        "granted": True,
        "catalog_default": True,
        "explicit": False,
    }
    assert all(row["capability"] != "roles.assign" for row in defaults["admin"])
    revisions = (
        _revision(users_app, ADMIN),
        _revision(users_app, PEER_ADMIN),
        _revision(users_app, USER),
    )
    body = {"defaults": {"accounts.list": False}}
    assert code(users_app.send("PATCH", "/api/roles/admin/defaults", OWNER, body)) == (
        403,
        "reauthentication_required",
    )
    changed = users_app.ok("PATCH", "/api/roles/admin/defaults", OWNER, {**body, **fresh(OWNER)})
    assert (
        next(row for row in changed["admin"] if row["capability"] == "accounts.list")["granted"]
        is False
    )
    assert (
        _revision(users_app, ADMIN),
        _revision(users_app, PEER_ADMIN),
        _revision(users_app, USER),
    ) == (
        revisions[0] + 1,
        revisions[1] + 1,
        revisions[2],
    )
    assert code(users_app.send("GET", "/api/users", ADMIN)) == (403, "capability_denied")
    users_app.ok(
        "PATCH",
        "/api/roles/admin/defaults",
        OWNER,
        {"defaults": {"accounts.list": None}, **fresh(OWNER)},
    )
    assert users_app.send("GET", "/api/users", ADMIN).status_code == 200
    owner_power = {"defaults": {"connections.edit": True}, **fresh(OWNER)}
    assert code(users_app.send("PATCH", "/api/roles/user/defaults", OWNER, owner_power)) == (
        422,
        "owner_only_capability",
    )
    assert (
        users_app.send("PATCH", "/api/roles/owner/defaults", OWNER, owner_power).status_code == 422
    )
    events = [json.loads(row[4]) for row in users_app.audit() if row[0] == "permissions.change"]
    assert events[-2:] == [
        {"change": "role_defaults", "role": "admin", "changed": 1},
        {"change": "role_defaults", "role": "admin", "changed": 1},
    ]


@pytest.mark.parametrize("role", ["admin", "user"])
@pytest.mark.parametrize(
    "capability,granted", [("scans.start", True), ("filters.preferences.edit.other", False)]
)
def test_role_defaults_distinguish_explicit_catalog_values_and_inheritance(
    users_app, role, capability, granted
):
    def stored():
        defaults = users_app.ok("GET", "/api/roles/defaults", OWNER)
        return next(row for row in defaults[role] if row["capability"] == capability)

    initial = stored()
    assert initial["catalog_default"] is granted
    assert initial["explicit"] is False
    path = f"/api/roles/{role}/defaults"
    body = {"defaults": {capability: granted}, **fresh(OWNER)}
    changed = users_app.ok("PATCH", path, OWNER, body)
    expected = {**initial, "explicit": True}
    assert next(row for row in changed[role] if row["capability"] == capability) == expected
    assert stored() == expected
    before = _revision(users_app, ADMIN if role == "admin" else USER)
    users_app.ok("PATCH", path, OWNER, body)
    assert stored() == expected
    assert _revision(users_app, ADMIN if role == "admin" else USER) == before
    users_app.ok("PATCH", path, OWNER, {"defaults": {capability: None}, **fresh(OWNER)})
    assert stored() == initial


def test_archive_view_belongs_to_the_admin_role_and_no_grant_changes_it(users_app):
    refused = (422, "role_fixed_capability")
    path = f"/api/users/{USER}/permissions"
    allow = {"overrides": {"archive.view": "allow"}, **fresh(OWNER)}
    assert code(users_app.send("PATCH", path, OWNER, allow)) == refused
    deny = {"overrides": {"archive.view": "deny"}, **fresh(OWNER)}
    assert code(users_app.send("PATCH", f"/api/users/{ADMIN}/permissions", OWNER, deny)) == refused
    for role, granted in (("user", True), ("admin", False)):
        defaults = {"defaults": {"archive.view": granted}, **fresh(OWNER)}
        assert code(users_app.send("PATCH", f"/api/roles/{role}/defaults", OWNER, defaults)) == (
            refused
        )
    assert "archive.view" not in users_app.ok("GET", path, OWNER)["effective"]
    admin = users_app.ok("GET", f"/api/users/{ADMIN}/permissions", OWNER)
    assert "archive.view" in admin["effective"]
