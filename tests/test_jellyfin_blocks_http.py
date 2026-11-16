# ruff: noqa: F811
"""Deleting a linked account can block its Jellyfin user until the owner unblocks them.

`allow` keeps today's behavior: the next Jellyfin sign-in creates a fresh account.
`block` stores the Jellyfin server and user ID; password sign-in, Quick Connect,
import, and linking refuse that user with an audit event until an owner unblocks
them with a fresh password. Harness: `tests/test_jellyfin_quick_connect_http.py`.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from scanner.db import AuditEventRow
from tests.jellyfin_user_boundary import IDS, PRIMARY
from tests.multi_user_access_support import ADMIN, OWNER, USER
from tests.test_jellyfin_accounts_http import _linked, _sign_in
from tests.test_jellyfin_quick_connect_http import (
    _complete,
    _start,
    _status,
    app,  # noqa: F401
    boundary,  # noqa: F401
)
from tests.users_api_http_support import fresh

UNBLOCK = "/api/jellyfin/blocks/unblock"


def _delete(app, user_id, jellyfin_user=None, *, actor=OWNER):
    preview = app.ok("GET", f"/api/users/{user_id}/deletion?mode=retain", actor)
    body = {"mode": "retain", "confirmation": preview["confirmation"], **fresh(actor)}
    if jellyfin_user is not None:
        body["jellyfin_user"] = jellyfin_user
    return app.send("POST", f"/api/users/{user_id}/deletion", actor, body)


def _blocks(app, actor=OWNER):
    return app.ok("GET", "/api/jellyfin/blocks", actor)["blocks"]


def _events(app, action):
    return [
        (outcome, target, json.loads(details))
        for name, outcome, _actor, target, details in app.audit()
        if name == action
    ]


def _quick_connect(app, boundary, name):
    app.client.cookies.clear()
    code = _start(app)
    boundary.approve(code, name)
    _status(app)
    return _complete(app)


def _unblock(app, actor, name, *, proof=True):
    body = {"server_id": PRIMARY, "jellyfin_user_id": IDS[name], **(fresh(actor) if proof else {})}
    return app.send("POST", UNBLOCK, actor, body)


def test_allowing_them_back_lets_the_next_sign_in_create_a_fresh_account(app):
    assert _sign_in(app, "Casey").status_code == 200
    casey = _linked(app, "Casey")

    deleted = _delete(app, casey)

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["jellyfin_blocked"] is False
    assert _blocks(app) == []
    assert _sign_in(app, "Casey").status_code == 200
    assert _linked(app, "Casey") not in (None, casey)


def test_a_blocked_user_cannot_sign_in_by_password_or_quick_connect_or_be_imported(app, boundary):
    assert _sign_in(app, "Casey").status_code == 200
    casey = _linked(app, "Casey")

    deleted = _delete(app, casey, "block")

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["jellyfin_blocked"] is True
    [block] = _blocks(app, ADMIN)
    assert (block["server_id"], block["jellyfin_user_id"], block["name"], block["blocked_by"]) == (
        PRIMARY,
        IDS["Casey"],
        "Casey",
        OWNER,
    )
    assert _events(app, "account.delete")[-1][2]["jellyfin_blocked"] is True
    for refused in (_sign_in(app, "Casey"), _quick_connect(app, boundary, "Casey")):
        assert (refused.status_code, refused.json()["code"]) == (403, "jellyfin_user_blocked")
    assert _linked(app, "Casey") is None
    sign_ins = [event for event in _events(app, "auth.sign_in") if event[0] == "denied"]
    assert [event[2]["reason"] for event in sign_ins] == ["jellyfin_user_blocked"] * 2
    listed = app.ok("GET", "/api/jellyfin/users", OWNER)["users"]
    assert {user["name"]: user["blocked"] for user in listed}["Casey"] is True
    imported = app.ok("POST", "/api/jellyfin/users/import", OWNER, {"all": True})["results"]
    assert {result["name"]: result["outcome"] for result in imported}["Casey"] == "blocked"
    assert _linked(app, "Casey") is None
    denied = [event for event in _events(app, "account.create") if event[0] == "denied"]
    assert denied == [
        ("denied", None, {"method": "jellyfin_import", "reason": "jellyfin_user_blocked"})
    ]


def test_only_the_owner_unblocks_with_a_fresh_password_and_then_a_fresh_account_starts(app):
    assert _sign_in(app, "Casey").status_code == 200
    casey = _linked(app, "Casey")
    assert _delete(app, casey, "block").status_code == 200

    by_admin = _unblock(app, ADMIN, "Casey")
    without_proof = _unblock(app, OWNER, "Casey", proof=False)

    assert (by_admin.status_code, by_admin.json()["code"]) == (403, "capability_denied")
    assert (without_proof.status_code, without_proof.json()["code"]) == (
        403,
        "reauthentication_required",
    )
    unblocked = _unblock(app, OWNER, "Casey")
    assert unblocked.status_code == 200, unblocked.text
    assert unblocked.json() == {"blocks": []}
    assert _sign_in(app, "Casey").status_code == 200
    assert _linked(app, "Casey") not in (None, casey)
    malformed = app.send(
        "POST",
        UNBLOCK,
        OWNER,
        {"server_id": "not a guid", "jellyfin_user_id": IDS["Casey"], **fresh(OWNER)},
    )
    assert malformed.status_code == 422
    again = _unblock(app, OWNER, "Casey")
    assert (again.status_code, again.json()["code"]) == (404, "jellyfin_user_not_blocked")
    events = _events(app, "account.jellyfin.unblock")
    assert [(outcome, details.get("reason")) for outcome, _target, details in events] == [
        ("denied", "capability_denied"),
        ("denied", "reauthentication_required"),
        ("succeeded", None),
        ("denied", "jellyfin_user_not_blocked"),
    ]
    with app.accounts.factory() as db:
        resources = db.scalars(
            select(AuditEventRow.resource)
            .filter_by(action="account.jellyfin.unblock")
            .order_by(AuditEventRow.id)
        ).all()
    assert resources[2:] == [f"{PRIMARY}:{IDS['Casey']}"] * 2


def test_bulk_deletion_blocks_each_linked_target_and_ignores_local_ones(app):
    for name in ("Blake", "Casey"):
        assert _sign_in(app, name).status_code == 200
    targets = [_linked(app, "Blake"), _linked(app, "Casey"), USER]
    body = {
        "targets": [
            {
                "user_id": target,
                "mode": "retain",
                "jellyfin_user": "block",
                "confirmation": app.ok("GET", f"/api/users/{target}/deletion?mode=retain", OWNER)[
                    "confirmation"
                ],
            }
            for target in targets
        ],
        **fresh(OWNER),
    }

    results = app.ok("POST", "/api/users/bulk-deletion", OWNER, body)["results"]

    assert [(r["outcome"], r["result"]["jellyfin_blocked"]) for r in results] == [
        ("applied", True),
        ("applied", True),
        ("applied", False),
    ]
    assert sorted(block["name"] for block in _blocks(app)) == ["Blake", "Casey"]


def test_a_retried_deletion_replays_only_the_same_jellyfin_choice(app):
    assert _sign_in(app, "Casey").status_code == 200
    casey = _linked(app, "Casey")
    preview = app.ok("GET", f"/api/users/{casey}/deletion?mode=retain", OWNER)
    body = {"mode": "retain", "confirmation": preview["confirmation"], **fresh(OWNER)}
    path = f"/api/users/{casey}/deletion"
    assert app.send("POST", path, OWNER, {**body, "jellyfin_user": "block"}).status_code == 200

    replayed = app.send("POST", path, OWNER, {**body, "jellyfin_user": "block"})
    other = app.send("POST", path, OWNER, {**body, "jellyfin_user": "allow"})

    assert replayed.status_code == 200 and replayed.json()["replayed"] is True
    assert other.status_code == 404
