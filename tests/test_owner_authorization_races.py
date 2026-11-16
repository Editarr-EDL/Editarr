"""Ownership changes invalidate pending protected reads and writes."""

import threading
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from scanner.http import account_operations, api_key_api, users_api
from scanner.persistence.rows.db_auth import RoleCapabilityDefaultRow, UserProfileRow
from scanner.security.access.capability_ids import Capability
from tests.multi_user_access_support import ADMIN, OWNER, USER
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import code, fresh, png
from tests.users_api_http_support import users_app as users_app


@pytest.fixture(autouse=True)
def successors(users_app):
    """A successor must be linked to Jellyfin; these races hand ownership to both."""
    users_app.link(ADMIN)
    users_app.link(USER)


@contextmanager
def pending(action, entered, release):
    result = {}

    def execute():
        try:
            result["response"] = action()
        except BaseException as error:
            result["error"] = error

    worker = threading.Thread(target=execute, daemon=True)
    worker.start()
    try:
        assert entered.wait(15), "request did not reach the authorization boundary"
        yield result
    finally:
        release.set()
        worker.join(30)
        assert not worker.is_alive(), "request did not finish"
    if "error" in result:
        raise result["error"]


def test_former_owner_cannot_finish_revealing_the_new_owners_key(users_app, monkeypatch):
    users_app.client.app.include_router(api_key_api.router)
    entered, release = threading.Event(), threading.Event()
    actual = api_key_api._authorize

    def pause(*args, **kwargs):
        answer = actual(*args, **kwargs)
        if args[0].own.user_id == OWNER:
            entered.set()
            assert release.wait(20)
        return answer

    monkeypatch.setattr(api_key_api, "_authorize", pause)
    with pending(
        lambda: users_app.send("POST", f"/api/users/{ADMIN}/api-key/reveal", OWNER, fresh(OWNER)),
        entered,
        release,
    ) as result:
        users_app.ok("POST", f"/api/users/{ADMIN}/ownership", OWNER, fresh(OWNER))
        replacement = users_app.ok("POST", "/api/auth/api-key", ADMIN, fresh(ADMIN))["api_key"]
    response = result["response"]
    revealed_new_key = response.status_code == 200 and response.json().get("api_key") == replacement
    assert not revealed_new_key
    assert code(response) == (403, "capability_denied")


def test_former_owner_cannot_finish_editing_role_defaults(users_app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    actual = account_operations.authorized

    def pause(caller, proofs, capability, target, audited):
        answer = actual(caller, proofs, capability, target, audited)
        if capability is Capability.ROLES_DEFAULTS_EDIT:
            entered.set()
            assert release.wait(20)
        return answer

    monkeypatch.setattr(account_operations, "authorized", pause)
    with pending(
        lambda: users_app.send(
            "PATCH",
            "/api/roles/user/defaults",
            OWNER,
            {"defaults": {Capability.MODELS_DOWNLOAD.value: False}, **fresh(OWNER)},
        ),
        entered,
        release,
    ) as result:
        users_app.ok("POST", f"/api/users/{ADMIN}/ownership", OWNER, fresh(OWNER))
    with users_app.accounts.factory() as database:
        stored = database.scalar(
            select(RoleCapabilityDefaultRow.granted).where(
                RoleCapabilityDefaultRow.role == "user",
                RoleCapabilityDefaultRow.capability == Capability.MODELS_DOWNLOAD.value,
            )
        )
    assert stored is None
    assert code(result["response"]) == (403, "capability_denied")


@pytest.mark.parametrize(("actor", "successor"), [(OWNER, ADMIN), (ADMIN, USER)])
def test_a_pending_profile_edit_cannot_change_the_new_owner(
    users_app, monkeypatch, actor, successor
):
    entered, release = threading.Event(), threading.Event()
    actual = users_api.profile_values

    def pause(body, names):
        answer = actual(body, names)
        entered.set()
        assert release.wait(20)
        return answer

    monkeypatch.setattr(users_api, "profile_values", pause)
    with pending(
        lambda: users_app.send(
            "PATCH", f"/api/users/{successor}", actor, {"email": "former@example.test"}
        ),
        entered,
        release,
    ) as result:
        users_app.ok("POST", f"/api/users/{successor}/ownership", OWNER, fresh(OWNER))
    with users_app.accounts.factory() as database:
        email = database.scalar(
            select(UserProfileRow.email).where(UserProfileRow.user_id == successor)
        )
    assert email is None
    assert code(result["response"]) == (403, "protected_account")


@pytest.mark.parametrize(
    ("method", "actor", "successor", "refusal"),
    [
        ("POST", OWNER, ADMIN, "capability_denied"),
        ("DELETE", OWNER, ADMIN, "protected_account"),
        ("DELETE", ADMIN, USER, "protected_account"),
    ],
)
def test_pending_key_changes_preserve_the_successors_rotated_key(
    users_app, monkeypatch, method, actor, successor, refusal
):
    users_app.client.app.include_router(api_key_api.router)
    entered, release = threading.Event(), threading.Event()
    actual = api_key_api._authorize

    def pause(*args, **kwargs):
        answer = actual(*args, **kwargs)
        if args[0].own.user_id == actor:
            entered.set()
            assert release.wait(20)
        return answer

    monkeypatch.setattr(api_key_api, "_authorize", pause)
    with pending(
        lambda: users_app.send(
            method,
            f"/api/users/{successor}/api-key",
            actor,
            fresh(actor) if method == "POST" else None,
        ),
        entered,
        release,
    ) as result:
        users_app.ok("POST", f"/api/users/{successor}/ownership", OWNER, fresh(OWNER))
        replacement = users_app.ok("POST", "/api/auth/api-key", successor, fresh(successor))[
            "api_key"
        ]
    assert code(result["response"]) == (403, refusal)
    current = users_app.ok("POST", "/api/auth/api-key/reveal", successor, fresh(successor))[
        "api_key"
    ]
    unchanged = current == replacement
    assert unchanged


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_pending_avatar_changes_cannot_modify_a_promoted_owner(users_app, monkeypatch, method):
    path = f"/api/users/{USER}/avatar"
    users_app.unlink(USER)
    before = users_app.send("PUT", path, USER, content=png()).json()["avatar"]
    users_app.link(USER)
    entered, release = threading.Event(), threading.Event()
    actual = users_api._avatar_target

    def pause(*args):
        target = actual(*args)
        entered.set()
        assert release.wait(20)
        return target

    monkeypatch.setattr(users_api, "_avatar_target", pause)
    with pending(
        lambda: users_app.send(
            method,
            path,
            ADMIN,
            **({"content": png(color=(0, 0, 0, 255))} if method == "PUT" else {}),
        ),
        entered,
        release,
    ) as result:
        users_app.ok("POST", f"/api/users/{USER}/ownership", OWNER, fresh(OWNER))
    assert code(result["response"]) == (403, "protected_account")
    assert users_app.ok("GET", f"/api/users/{USER}", USER)["avatar"] == before


def test_pending_profile_fields_cannot_modify_a_promoted_owner(users_app, monkeypatch):
    path = f"/api/users/{USER}"
    before = users_app.ok("GET", path, USER)["email"]
    entered, release = threading.Event(), threading.Event()
    actual = users_api.profile_values

    def pause(*args):
        values = actual(*args)
        entered.set()
        assert release.wait(20)
        return values

    monkeypatch.setattr(users_api, "profile_values", pause)
    with pending(
        lambda: users_app.send("PATCH", path, ADMIN, {"email": "unauthorized@example.test"}),
        entered,
        release,
    ) as result:
        users_app.ok("POST", f"/api/users/{USER}/ownership", OWNER, fresh(OWNER))
    assert code(result["response"]) == (403, "protected_account")
    assert users_app.ok("GET", path, USER)["email"] == before
