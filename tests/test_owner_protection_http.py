"""MU-18: no account management path disables, demotes, or strips the owner."""

import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

import scanner.http.account_operations as account_operations
from scanner.persistence.rows.db_auth import AuthSessionRow, UserRow
from scanner.persistence.write_lock import write_lock
from scanner.security import account_permissions, account_repository
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.identity import DisableCause
from scanner.security.access.vocabulary import Role
from scanner.security.account_ownership import transfer_ownership
from tests.multi_user_access_support import (
    ADMIN,
    OWNER,
    PEER_ADMIN,
    PEER_USER,
    USER,
)
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import code, fresh
from tests.users_api_http_support import users_app as users_app

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def _owner_state(app) -> tuple:
    with app.accounts.factory() as db:
        row = db.get(UserRow, OWNER)
        sessions = db.scalar(
            select(func.count())
            .select_from(AuthSessionRow)
            .where(AuthSessionRow.user_id == OWNER, AuthSessionRow.revoked_at.is_(None))
        )
        return row.role, row.status, row.permission_revision, sessions


def _attempts(caller: int) -> dict[str, tuple]:
    proof = fresh(caller)
    target = f"/api/users/{OWNER}"
    return {
        "disable": ("PUT", f"{target}/status", {"status": "disabled"}),
        "demote": ("PUT", f"{target}/role", {"role": "user", **proof}),
        "end sessions": ("DELETE", f"{target}/sessions", None),
        "deny a capability": (
            "PATCH",
            f"{target}/permissions",
            {"overrides": {Capability.ACCOUNTS_LIST.value: "deny"}, **proof},
        ),
        "restrict media": ("PUT", f"{target}/media-access", {"sources": [], **proof}),
    }


def test_single_account_routes_refuse_every_change_to_the_owner(users_app):
    unchanged = _owner_state(users_app)
    answers = {
        (caller, name): code(users_app.send(method, path, caller, body))
        for caller in (OWNER, ADMIN)
        for name, (method, path, body) in _attempts(caller).items()
    }
    assert answers == {
        (OWNER, "disable"): (409, "own_account"),
        (OWNER, "demote"): (409, "own_account"),
        (OWNER, "end sessions"): (409, "own_account"),
        (OWNER, "deny a capability"): (409, "own_account"),
        (OWNER, "restrict media"): (409, "own_account"),
        (ADMIN, "disable"): (403, "protected_account"),
        (ADMIN, "demote"): (403, "capability_denied"),
        (ADMIN, "end sessions"): (403, "protected_account"),
        (ADMIN, "deny a capability"): (403, "capability_denied"),
        (ADMIN, "restrict media"): (403, "protected_account"),
    }
    assert _owner_state(users_app) == unchanged
    assert unchanged[:2] == ("owner", "active") and unchanged[3] > 0


@pytest.mark.parametrize(
    ("caller", "body", "applied"),
    [
        (ADMIN, {"action": "disable"}, PEER_USER),
        (ADMIN, {"action": "revoke_sessions"}, PEER_USER),
        (OWNER, {"action": "disable"}, PEER_USER),
        (OWNER, {"action": "set_role", "role": "user", **fresh(OWNER)}, PEER_ADMIN),
        (ADMIN, {"action": "set_media_access", "media_access": {}, **fresh(ADMIN)}, PEER_USER),
        (OWNER, {"action": "set_media_access", "media_access": {}, **fresh(OWNER)}, PEER_USER),
    ],
)
def test_bulk_requests_refuse_the_owner_and_apply_the_rest(users_app, caller, body, applied):
    unchanged = _owner_state(users_app)
    response = users_app.ok(
        "POST", "/api/users/bulk", caller, {**body, "user_ids": [OWNER, applied]}
    )
    refusal = "own_account" if caller == OWNER else "protected_account"
    assert response["results"] == [
        {"user_id": OWNER, "outcome": "refused", "code": refusal},
        {"user_id": applied, "outcome": "applied", "code": None},
    ]
    assert _owner_state(users_app) == unchanged


def test_role_defaults_never_reach_the_owner(users_app):
    denied = {"defaults": {Capability.ACCOUNTS_LIST.value: False}, **fresh(OWNER)}
    assert users_app.send("PATCH", "/api/roles/owner/defaults", OWNER, denied).status_code == 422
    users_app.ok("PATCH", "/api/roles/admin/defaults", OWNER, denied)
    capabilities = users_app.ok("GET", "/api/test-access/payload", OWNER)["capabilities"]
    assert len(capabilities) == len(Capability)
    assert (
        Capability.ACCOUNTS_LIST.value
        not in users_app.ok("GET", "/api/test-access/payload", ADMIN)["capabilities"]
    )


def test_owner_rows_refuse_storage_writes_outside_a_transfer(accounts):
    with accounts.factory() as db:
        with pytest.raises(AccessDenied) as disabled:
            account_repository.set_disable_cause(db, OWNER, DisableCause.LOCAL, now=NOW)
        assert account_permissions.assign_role(db, OWNER, Role.ADMIN, now=NOW) is False
        db.commit()
    assert disabled.value.code.value == "protected_account"
    with accounts.factory() as db:
        row = db.get(UserRow, OWNER)
        assert (row.role, row.status) == ("owner", "active")


def test_a_transfer_from_an_account_that_is_not_the_owner_changes_nothing(accounts):
    accounts.link(USER)
    with accounts.factory() as db:
        before = dict(db.execute(select(UserRow.id, UserRow.role)).all())
        with pytest.raises(AccessDenied) as refused, write_lock(db):
            transfer_ownership(db, ADMIN, USER, now=NOW)
    assert refused.value.code.value == "capability_denied"
    with accounts.factory() as db:
        assert dict(db.execute(select(UserRow.id, UserRow.role)).all()) == before


def test_a_request_the_former_owner_sent_before_a_transfer_cannot_act_as_owner(
    users_app, monkeypatch
):
    """The demotion lands while the former owner's role change waits for the write lock."""

    waiting, release = threading.Event(), threading.Event()
    real_lock = account_operations.write_lock
    calls = []

    def write_lock(db):
        calls.append(None)
        if len(calls) == 1:
            waiting.set()
            assert release.wait(10)
        return real_lock(db)

    users_app.link(ADMIN)
    monkeypatch.setattr(account_operations, "write_lock", write_lock)
    result = {}

    def demote_admin() -> None:
        body = {"role": "user", **fresh(OWNER)}
        result["role"] = code(users_app.send("PUT", f"/api/users/{ADMIN}/role", OWNER, body))

    stale = threading.Thread(target=demote_admin)
    stale.start()
    assert waiting.wait(10)
    users_app.ok("POST", f"/api/users/{ADMIN}/ownership", OWNER, fresh(OWNER))
    release.set()
    stale.join(timeout=30)

    assert result["role"] == (403, "capability_denied")
    with users_app.accounts.factory() as db:
        roles = dict(db.execute(select(UserRow.id, UserRow.role)).all())
    assert (roles[ADMIN], roles[OWNER], roles[USER]) == ("owner", "admin", "user")
