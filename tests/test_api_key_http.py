"""MU-13 acceptance through HTTP: per-user keys across sessions, keys, roles, and targets.

Harness: `tests.api_key_http_support` (live accounts owner 1, admins 2 and 3,
users 4 and 5, each with a session and a key).
"""

from __future__ import annotations

import hashlib
import threading

from sqlalchemy import select

from scanner.db import ApiKeyRow
from scanner.security.access.capability_ids import Capability
from scanner.security.access.identity import AccountStatus
from scanner.security.access.overrides import OverrideEffect
from tests.api_key_http_support import accounts as accounts
from tests.api_key_http_support import code, password_of
from tests.api_key_http_support import key_app as key_app
from tests.multi_user_access_support import (
    ADMIN,
    MEMBER_PASSWORD,
    OWNER,
    OWNER_PASSWORD,
    PEER_ADMIN,
    PEER_USER,
    USER,
)

_OTHERS = (OWNER, ADMIN, PEER_ADMIN, PEER_USER)


def test_rotating_one_key_invalidates_only_that_key(accounts, key_app):
    app = key_app()
    old = accounts.keys[USER]
    rotated = app.issue(accounts.unsafe_cookie(USER), MEMBER_PASSWORD)
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()["api_key"]
    assert new != old and len(new) == 64
    assert code(app.send("GET", "/api/test-access/payload", {"X-Api-Key": old})) == (
        401,
        "invalid_api_key",
    )
    assert app.works(new) == 200
    assert [app.works(accounts.keys[other]) for other in _OTHERS] == [200] * len(_OTHERS)
    assert app.reveal(accounts.unsafe_cookie(USER), MEMBER_PASSWORD).json()["api_key"] == new


def test_disabling_an_account_blocks_its_key_on_the_next_request(accounts, key_app):
    app = key_app()
    assert app.works(accounts.keys[USER]) == 200
    accounts.set_status(USER, AccountStatus.DISABLED)
    refused = app.send("GET", "/api/test-access/payload", accounts.key(USER))
    assert code(refused) == (403, "account_disabled")
    assert app.works(accounts.keys[PEER_USER]) == 200
    accounts.set_status(USER, AccountStatus.ACTIVE)
    assert app.works(accounts.keys[USER]) == 200


def test_the_holder_and_owner_reveal_after_fresh_authentication(accounts, key_app):
    app = key_app()
    holder = accounts.unsafe_cookie(USER)
    assert code(app.reveal(holder, None)) == (403, "reauthentication_required")
    assert code(app.reveal(holder, OWNER_PASSWORD)) == (403, "reauthentication_failed")
    shown = app.reveal(holder, MEMBER_PASSWORD)
    assert shown.status_code == 200
    assert shown.json()["api_key"] == accounts.keys[USER]
    assert shown.headers["cache-control"] == "no-store"
    assert code(app.reveal(holder, None)) == (403, "reauthentication_required")

    by_owner = app.reveal(accounts.unsafe_cookie(OWNER), OWNER_PASSWORD, holder=USER)
    assert by_owner.json()["api_key"] == accounts.keys[USER]
    own_by_path = app.reveal(holder, MEMBER_PASSWORD, holder=USER)
    assert own_by_path.json()["api_key"] == accounts.keys[USER]


def test_an_admin_cannot_reveal_or_issue_another_users_key(accounts, key_app):
    app = key_app()
    admin = accounts.unsafe_cookie(ADMIN)
    for target, expected in (
        (USER, (403, "capability_denied")),
        (PEER_ADMIN, (403, "capability_denied")),
        (OWNER, (403, "capability_denied")),
    ):
        assert code(app.reveal(admin, MEMBER_PASSWORD, holder=target)) == expected
        assert code(app.issue(admin, MEMBER_PASSWORD, holder=target)) == expected
    member = accounts.unsafe_cookie(USER)
    for target in (PEER_USER, ADMIN, OWNER, 99):
        assert code(app.reveal(member, MEMBER_PASSWORD, holder=target)) == (404, "not_found")
    assert [app.works(accounts.keys[user]) for user in (USER, PEER_ADMIN, OWNER)] == [200] * 3


def test_admins_revoke_ordinary_users_keys_only(accounts, key_app):
    app = key_app()
    admin = accounts.unsafe_cookie(ADMIN)
    assert app.revoke(admin, holder=USER).json() == {
        "configured": False,
        "created_at": None,
        "revealable": False,
    }
    assert app.works(accounts.keys[USER]) == 401
    assert code(app.revoke(admin, holder=PEER_ADMIN)) == (403, "protected_account")
    assert code(app.revoke(admin, holder=OWNER)) == (403, "protected_account")
    assert code(app.revoke(accounts.unsafe_cookie(PEER_USER), holder=ADMIN)) == (404, "not_found")
    assert [app.works(accounts.keys[user]) for user in (PEER_ADMIN, OWNER, PEER_USER)] == [200] * 3
    assert app.revoke(accounts.unsafe_cookie(OWNER), holder=PEER_ADMIN).status_code == 200
    assert app.works(accounts.keys[PEER_ADMIN]) == 401


def test_the_owner_issues_another_users_key_and_the_holder_reveals_it(accounts, key_app):
    app = key_app()
    issued = app.issue(accounts.unsafe_cookie(OWNER), OWNER_PASSWORD, holder=PEER_ADMIN)
    assert issued.status_code == 200
    assert app.works(accounts.keys[PEER_ADMIN]) == 401
    assert app.works(issued.json()["api_key"]) == 200
    shown = app.reveal(accounts.unsafe_cookie(PEER_ADMIN), MEMBER_PASSWORD)
    assert shown.json()["api_key"] == issued.json()["api_key"]


def test_a_fully_privileged_key_cannot_run_any_interactive_only_action(accounts, key_app):
    app = key_app()
    owner_key = accounts.key(OWNER)
    attempts = [
        ("POST", "/api/auth/api-key"),
        ("POST", "/api/auth/api-key/reveal"),
        ("POST", f"/api/users/{USER}/api-key"),
        ("POST", f"/api/users/{USER}/api-key/reveal"),
    ]
    for method, path in attempts:
        refused = app.send(
            method, path, owner_key, {"reauthentication": {"password": OWNER_PASSWORD}}
        )
        assert code(refused) == (403, "interactive_session_required"), path
    assert app.works(accounts.keys[OWNER]) == app.works(accounts.keys[USER]) == 200


def test_production_marks_exactly_the_interactive_only_routes():
    from scanner.api import app
    from scanner.security.access.route_match import _route_templates, interactive_only

    marked = set()
    for route, template in _route_templates(app.router.routes):
        for method in sorted(getattr(route, "methods", None) or ()):
            path = template.replace("{user_id}", "4").replace("{job_id}", "job")
            scope = {"type": "http", "method": method, "path": path, "root_path": ""}
            if interactive_only(app, scope):
                marked.add((method, template))
    assert marked == {
        ("POST", "/api/auth/api-key"),
        ("POST", "/api/auth/api-key/reveal"),
        ("POST", "/api/users/{user_id}/api-key"),
        ("POST", "/api/users/{user_id}/api-key/reveal"),
        ("PUT", "/api/settings/filters/users/{user_id}/policy"),
        ("PUT", "/api/users/{user_id}/role"),
        ("POST", "/api/users/{user_id}/link/proof"),
        ("POST", "/api/users/{user_id}/link"),
        ("GET", "/api/users/{user_id}/deletion"),
        ("POST", "/api/users/{user_id}/deletion"),
        ("POST", "/api/users/bulk-deletion"),
        ("POST", "/api/jellyfin/blocks/unblock"),
        ("POST", "/api/users/{user_id}/ownership"),
        ("GET", "/api/users/{user_id}/permissions"),
        ("PATCH", "/api/users/{user_id}/permissions"),
        ("GET", "/api/roles/defaults"),
        ("PATCH", "/api/roles/{role}/defaults"),
        ("GET", "/api/media-access/default"),
        ("PUT", "/api/media-access/default"),
        ("GET", "/api/users/{user_id}/media-access"),
        ("PUT", "/api/users/{user_id}/media-access"),
    }


def test_permission_edits_affect_the_next_key_request(accounts, key_app):
    app = key_app()
    key = accounts.key(USER)
    assert app.send("GET", "/api/auth/api-key", key).json()["configured"] is True
    accounts.set_override(USER, Capability.API_KEYS_REVOKE_OWN, OverrideEffect.DENY)
    assert code(app.revoke(key)) == (403, "capability_denied")
    accounts.set_override(USER, Capability.PROFILE_VIEW_OWN, OverrideEffect.DENY)
    assert code(app.send("GET", "/api/auth/api-key", key)) == (403, "capability_denied")
    accounts.set_override(USER, Capability.PROFILE_VIEW_OWN, OverrideEffect.INHERIT)
    accounts.set_override(USER, Capability.API_KEYS_REVOKE_OWN, OverrideEffect.INHERIT)
    assert app.revoke(key).status_code == 200
    assert app.works(accounts.keys[USER]) == 401


def test_concurrent_rotations_leave_exactly_one_working_key(accounts, key_app):
    app = key_app()
    session = accounts.unsafe_cookie(USER)
    results, start = [], threading.Barrier(4)

    def rotate():
        start.wait()
        for _ in range(3):
            results.append(app.issue(session, MEMBER_PASSWORD))

    threads = [threading.Thread(target=rotate) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert {response.status_code for response in results} <= {200, 503}
    issued = [r.json()["api_key"] for r in results if r.status_code == 200]
    assert issued and len(set(issued)) == len(issued)
    working = [key for key in issued if app.works(key) == 200]
    assert len(working) == 1
    with accounts.factory() as db:
        digest = db.scalar(select(ApiKeyRow.token_digest).where(ApiKeyRow.user_id == USER))
    assert digest == hashlib.sha256(working[0].encode()).hexdigest()
    assert app.reveal(session, MEMBER_PASSWORD).json()["api_key"] == working[0]
    assert app.works(accounts.keys[PEER_USER]) == 200


def test_keys_are_never_stored_in_plaintext(accounts, key_app):
    app = key_app()
    issued = app.issue(accounts.unsafe_cookie(OWNER), OWNER_PASSWORD).json()["api_key"]
    files = [accounts.path, accounts.path.with_name(accounts.path.name + "-wal")]
    stored = b"".join(path.read_bytes() for path in files if path.exists())
    for key in (issued, *(accounts.keys[user] for user in (ADMIN, USER))):
        assert key.encode() not in stored
        assert bytes.fromhex(key) not in stored
    assert password_of(OWNER).encode() not in stored
