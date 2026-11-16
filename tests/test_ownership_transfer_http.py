"""MU-18: ownership transfer over the real Users routes and authentication middleware."""

import json
import threading

import pytest
from sqlalchemy import select

import scanner.http.account_operations as account_operations
from scanner.persistence.rows.db_auth import UserProviderIdentityRow, UserRow
from scanner.security import account_ownership
from tests.multi_user_access_support import (
    ADMIN,
    MEMBER_PASSWORD,
    OWNER,
    PEER_ADMIN,
    PEER_USER,
    USER,
)
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import code, fresh
from tests.users_api_http_support import users_app as users_app


def _path(user_id: int) -> str:
    return f"/api/users/{user_id}/ownership"


def _roles(app) -> dict[int, tuple[str, int]]:
    with app.accounts.factory() as db:
        rows = db.execute(select(UserRow.id, UserRow.role, UserRow.permission_revision)).all()
    return {user_id: (role, revision) for user_id, role, revision in rows}


def _owners(app) -> list[int]:
    return [user_id for user_id, (role, _) in _roles(app).items() if role == "owner"]


def _transfers(app) -> list[tuple]:
    return [
        (outcome, actor, target, json.loads(details))
        for action, outcome, actor, target, details in app.audit()
        if action == "account.ownership.transfer"
    ]


def _capabilities(app, user_id: int) -> list[str]:
    return app.ok("GET", "/api/test-access/payload", user_id)["capabilities"]


def test_the_owner_hands_over_and_becomes_an_admin_like_any_other(users_app):
    users_app.link(ADMIN)
    before = _roles(users_app)
    admin_capabilities = _capabilities(users_app, PEER_ADMIN)
    response = users_app.ok("POST", _path(ADMIN), OWNER, fresh(OWNER))
    assert response == {"owner_id": ADMIN, "former_owner_id": OWNER, "former_owner_role": "admin"}

    after = _roles(users_app)
    assert _owners(users_app) == [ADMIN]
    assert after[OWNER] == ("admin", before[OWNER][1] + 1)
    assert after[ADMIN] == ("owner", before[ADMIN][1] + 1)
    assert {user: after[user] for user in (PEER_ADMIN, USER, PEER_USER)} == {
        user: before[user] for user in (PEER_ADMIN, USER, PEER_USER)
    }
    assert _capabilities(users_app, OWNER) == admin_capabilities
    assert set(_capabilities(users_app, ADMIN)) > set(admin_capabilities)
    assert _transfers(users_app) == [("succeeded", OWNER, ADMIN, {"successor_role": "admin"})]

    role = {"role": "admin", **fresh(OWNER)}
    assert code(users_app.send("PUT", f"/api/users/{USER}/role", OWNER, role)) == (
        403,
        "capability_denied",
    )
    assert code(users_app.send("POST", _path(USER), OWNER, fresh(OWNER))) == (
        403,
        "capability_denied",
    )
    disable = users_app.send("PUT", f"/api/users/{USER}/status", OWNER, {"status": "disabled"})
    assert disable.status_code == 200, disable.text
    assert code(
        users_app.send("PUT", f"/api/users/{ADMIN}/status", OWNER, {"status": "disabled"})
    ) == (403, "protected_account")

    users_app.ok("POST", _path(OWNER), ADMIN, fresh(ADMIN))
    assert _owners(users_app) == [OWNER]
    assert _roles(users_app)[ADMIN][0] == "admin"


def test_only_the_owner_in_a_session_with_its_password_may_transfer(users_app):
    path = _path(USER)
    refusals = {
        "owner API key": users_app.send("POST", path, OWNER, fresh(OWNER), key=True),
        "admin": users_app.send("POST", path, ADMIN, fresh(ADMIN)),
        "user": users_app.send("POST", _path(PEER_USER), USER, fresh(USER)),
        "own account": users_app.send("POST", _path(OWNER), OWNER, fresh(OWNER)),
        "no password": users_app.send("POST", path, OWNER, {}),
        "wrong password": users_app.send(
            "POST", path, OWNER, {"reauthentication": {"password": MEMBER_PASSWORD}}
        ),
        "unknown account": users_app.send("POST", _path(999), OWNER, fresh(OWNER)),
    }
    assert {name: code(response) for name, response in refusals.items()} == {
        "owner API key": (403, "interactive_session_required"),
        "admin": (403, "capability_denied"),
        "user": (404, "not_found"),
        "own account": (409, "own_account"),
        "no password": (403, "reauthentication_required"),
        "wrong password": (403, "reauthentication_failed"),
        "unknown account": (404, "not_found"),
    }
    assert _owners(users_app) == [OWNER]
    assert [
        (outcome, actor, target, details["reason"])
        for outcome, actor, target, details in _transfers(users_app)
    ] == [
        ("denied", ADMIN, USER, "capability_denied"),
        ("denied", USER, PEER_USER, "not_found"),
        ("denied", OWNER, USER, "reauthentication_required"),
        ("failed", OWNER, USER, "reauthentication_failed"),
    ]


def test_the_successor_needs_an_active_account_linked_to_the_configured_server(users_app):
    users_app.link(PEER_USER)
    users_app.ok("PUT", f"/api/users/{PEER_USER}/status", OWNER, {"status": "disabled"})
    users_app.link(PEER_ADMIN)
    with users_app.accounts.factory() as db:
        db.get(UserProviderIdentityRow, PEER_ADMIN).link_state = "server_replaced"
        db.commit()

    offered = {
        user: users_app.ok("GET", f"/api/users/{user}", OWNER)["successor_refusal"]
        for user in (USER, PEER_USER, PEER_ADMIN)
    }
    refused = {
        user: code(users_app.send("POST", _path(user), OWNER, fresh(OWNER)))
        for user in (USER, PEER_USER, PEER_ADMIN)
    }
    assert offered == {user: reason for user, (_, reason) in refused.items()}
    assert refused == {
        USER: (409, "successor_credentials_required"),
        PEER_USER: (409, "successor_disabled"),
        PEER_ADMIN: (409, "successor_credentials_required"),
    }
    assert _owners(users_app) == [OWNER]
    assert _roles(users_app)[OWNER][0] == "owner"
    assert [
        (outcome, target, details["reason"])
        for outcome, _, target, details in _transfers(users_app)
    ] == [
        ("denied", USER, "successor_credentials_required"),
        ("denied", PEER_USER, "successor_disabled"),
        ("denied", PEER_ADMIN, "successor_credentials_required"),
    ]


def test_a_jellyfin_linked_account_may_become_the_owner(users_app):
    users_app.link(PEER_ADMIN)

    assert users_app.ok("GET", f"/api/users/{PEER_ADMIN}", OWNER)["successor_refusal"] is None
    response = users_app.ok("POST", _path(PEER_ADMIN), OWNER, fresh(OWNER))

    assert response["owner_id"] == PEER_ADMIN
    assert _owners(users_app) == [PEER_ADMIN]
    assert _roles(users_app)[OWNER][0] == "admin"


def test_concurrent_transfers_leave_exactly_one_owner(users_app, monkeypatch):
    """Both requests load the owner's access before either commits; one wins, one is stale."""

    entered = threading.Barrier(2, timeout=10)
    real_lock = account_operations.write_lock

    def write_lock(db):
        entered.wait()
        return real_lock(db)

    users_app.link(ADMIN)
    users_app.link(USER)
    monkeypatch.setattr(account_operations, "write_lock", write_lock)
    results: dict[int, tuple] = {}

    def transfer(user_id: int) -> None:
        results[user_id] = code(users_app.send("POST", _path(user_id), OWNER, fresh(OWNER)))

    threads = [threading.Thread(target=transfer, args=(user,)) for user in (ADMIN, USER)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results.values()) == [(200, None), (409, "permission_revision_stale")]
    winner = next(user for user, result in results.items() if result == (200, None))
    loser = USER if winner == ADMIN else ADMIN
    roles = _roles(users_app)
    assert _owners(users_app) == [winner]
    assert roles[OWNER][0] == "admin"
    assert roles[loser][0] == {ADMIN: "admin", USER: "user"}[loser]
    outcomes = sorted((outcome, target) for outcome, _, target, _ in _transfers(users_app))
    assert outcomes == sorted([("succeeded", winner), ("denied", loser)])


def test_a_transfer_is_one_transaction(users_app, monkeypatch):
    """A failure after the demotion leaves the original owner in place."""

    def interrupted(db, owner_id, successor_id, *, now):
        real(db, owner_id, successor_id, now=now)
        raise RuntimeError("interrupted after both role changes were staged")

    users_app.link(ADMIN)
    real = account_ownership.transfer_ownership
    monkeypatch.setattr(account_ownership, "transfer_ownership", interrupted)
    before = _roles(users_app)
    with pytest.raises(RuntimeError, match="interrupted"):
        users_app.send("POST", _path(ADMIN), OWNER, fresh(OWNER))
    assert _roles(users_app) == before
    assert _transfers(users_app) == []


def test_only_the_owner_learns_why_an_account_cannot_become_owner(users_app):
    users_app.ok("PUT", f"/api/users/{PEER_USER}/status", OWNER, {"status": "disabled"})

    as_owner = users_app.ok("GET", f"/api/users/{PEER_USER}", OWNER)
    as_admin = users_app.ok("GET", f"/api/users/{PEER_USER}", ADMIN)

    assert as_owner["successor_refusal"] == "successor_disabled"
    assert as_admin["successor_refusal"] is None
