"""Ownership transfer clears enforcement and fences delayed filter and registration writes."""

import json
import threading
from contextlib import contextmanager

import pytest
from sqlalchemy import select

import scanner.api  # noqa: F401
import scanner.http.catalog_settings_api as catalog_settings_api
import scanner.http.filter_policy_api as filter_policy_api
from scanner.http.auth_rate_limit import LoginRateLimiter
from scanner.http.reauthentication import ReauthenticationGuard
from scanner.persistence.rows.db_auth import UserRow
from scanner.persistence.rows.db_policies import UserFilterEnforcementRow, UserFilterPreferenceRow
from scanner.security import account_ownership
from tests.filter_policy_http_support import BASE, FilterApp, code, rule
from tests.multi_user_access_support import ADMIN, MEMBER_PASSWORD, OWNER, OWNER_PASSWORD, USER
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import fresh
from tests.users_api_http_support import users_app as users_app


@pytest.fixture
def app(users_app, monkeypatch):
    guard = ReauthenticationGuard(LoginRateLimiter(configuration=users_app.accounts.configuration))
    monkeypatch.setattr(filter_policy_api, "reauthentication_guard", guard)
    client = users_app.client
    client.app.include_router(filter_policy_api.router)
    client.app.include_router(catalog_settings_api.router)
    # A successor must be linked to Jellyfin; tests hand ownership to the admin or the user.
    users_app.link(ADMIN)
    users_app.link(USER)
    return users_app, FilterApp(client, users_app.accounts)


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
        assert entered.wait(15), "request did not reach the selected boundary"
        yield result
    finally:
        release.set()
        worker.join(30)
        assert not worker.is_alive(), "request did not finish"
    if "error" in result:
        raise result["error"]


def owners(users):
    with users.accounts.factory() as database:
        return database.scalars(select(UserRow.id).where(UserRow.role == "owner")).all()


def policy(filters, target, rules=()):
    revision = filters.ok("GET", f"{BASE}/personal", target)["policy_revision"]
    return filters.send(
        "PUT",
        f"{BASE}/users/{target}/policy",
        OWNER,
        {
            "policy_revision": revision,
            "rules": list(rules),
            "severities": [],
            "reauthentication": {"password": OWNER_PASSWORD},
        },
    )


def test_transfer_clears_the_successors_enforced_policy(app):
    users, filters = app
    required, other = rule("deny", category="language"), rule("allow", category="sex")
    filters.set_instance([required, other])
    personal = rule("deny", category="violence")
    assert (
        filters.live(
            ADMIN,
            personal_rules=[personal],
            severity_overrides=[{"subcategory": "swearing", "severity": "low"}],
        ).status_code
        == 200
    )
    assert filters.enforce(OWNER, ADMIN, [required["id"]], ["swearing"]).status_code == 200
    before = filters.view(ADMIN)
    users.ok("POST", f"/api/users/{ADMIN}/ownership", OWNER, fresh(OWNER))
    assert owners(users) == [ADMIN]
    view = filters.view(ADMIN)
    assert view["enforced_rules"] == []
    assert view["enforced_severities"] == []
    assert not view["mode_locked"]
    assert view["policy_revision"] == before["policy_revision"] + 1
    assert personal["id"] in [item["rule"]["id"] for item in view["rules"]]
    assert (
        next(item["severity"] for item in view["severities"] if item["subcategory"] == "swearing")
        == "low"
    )
    assert [
        (outcome, actor, target, json.loads(details))
        for action, outcome, actor, target, details in users.audit()
        if action == "filters.policy.change"
        and json.loads(details).get("cause") == "ownership_transfer"
    ] == [
        (
            "succeeded",
            OWNER,
            ADMIN,
            {
                "cause": "ownership_transfer",
                "policy_revision": before["policy_revision"] + 1,
                "rules": 0,
                "severities": 0,
            },
        )
    ]
    assert filters.mode(ADMIN, "copy").status_code == 200
    instance = filters.ok("GET", BASE, ADMIN)
    assert (
        filters.send(
            "PUT",
            BASE,
            ADMIN,
            {
                "revision": instance["revision"],
                "selection_rules": [other],
                "severity_defaults": instance["severity_defaults"],
                "reauthentication": {"password": MEMBER_PASSWORD},
            },
        ).status_code
        == 200
    )


def test_transfer_without_enforcement_does_not_change_policy_revision(app):
    users, filters = app
    before = filters.view(ADMIN)
    users.ok("POST", f"/api/users/{ADMIN}/ownership", OWNER, fresh(OWNER))
    assert filters.view(ADMIN)["policy_revision"] == before["policy_revision"]
    assert not [event for event in users.audit() if event[0] == "filters.policy.change"]


def test_interrupted_transfer_rolls_back_policy_roles_and_audit_together(app, monkeypatch):
    users, filters = app
    required = rule("deny", category="language")
    filters.set_instance([required])
    assert filters.enforce(OWNER, ADMIN, [required["id"]], ["swearing"]).status_code == 200
    before = filters.tables()
    actual = account_ownership.transfer_ownership

    def interrupted(*args, **kwargs):
        actual(*args, **kwargs)
        raise RuntimeError("interrupt after staging the transfer and policy events")

    monkeypatch.setattr(account_ownership, "transfer_ownership", interrupted)
    with pytest.raises(RuntimeError, match="interrupt after staging"):
        users.send("POST", f"/api/users/{ADMIN}/ownership", OWNER, fresh(OWNER))
    assert filters.tables() == before
    assert owners(users) == [OWNER]


def test_instance_edit_refuses_legacy_enforcement_on_the_owner_without_crashing(app):
    users, filters = app
    required = rule("deny", category="language")
    filters.set_instance([required])
    assert filters.enforce(OWNER, ADMIN, [required["id"]]).status_code == 200
    with users.accounts.factory() as database:
        database.get_one(UserFilterPreferenceRow, OWNER).policy_revision = 1
        database.flush()
        database.add(
            UserFilterEnforcementRow(user_id=OWNER, kind="selection_rule", target=required["id"])
        )
        database.commit()
    before = filters.instance()
    response = filters.send(
        "PUT",
        BASE,
        OWNER,
        {
            "revision": before["revision"],
            "selection_rules": [],
            "severity_defaults": before["severity_defaults"],
            **fresh(OWNER),
        },
    )
    assert code(response) == (409, "owner_filter_policy")
    assert filters.instance() == before
    assert filters.view(OWNER)["enforced_rules"] == [required["id"]]


@pytest.mark.parametrize("endpoint", ["mode", "preferences"])
def test_pending_filter_write_cannot_change_a_promoted_owner(app, monkeypatch, endpoint):
    users, filters = app
    entered, release = threading.Event(), threading.Event()
    actual = filter_policy_api.require
    action = filter_policy_api._MODE if endpoint == "mode" else filter_policy_api._VALUES

    def pause(access, capability, *args, **kwargs):
        answer = actual(access, capability, *args, **kwargs)
        if capability == action and not entered.is_set():
            entered.set()
            assert release.wait(20)
        return answer

    monkeypatch.setattr(filter_policy_api, "require", pause)
    before = filters.ok("GET", f"{BASE}/users/{USER}", OWNER)
    body = {"revision": before["revision"], "mode": "copy" if endpoint == "mode" else "live"}
    suffix = "/mode" if endpoint == "mode" else ""
    actor = ADMIN if endpoint == "mode" else OWNER
    with pending(
        lambda: filters.send("PUT", f"{BASE}/users/{USER}{suffix}", actor, body), entered, release
    ) as result:
        users.ok("POST", f"/api/users/{USER}/ownership", OWNER, fresh(OWNER))
    assert result["response"].status_code == 403, result["response"].text
    after = filters.view(USER)
    assert after["mode"] == before["mode"]
    assert after["revision"] == before["revision"]
    assert owners(users) == [USER]


def test_former_owner_cannot_finish_enforcing_a_policy(app, monkeypatch):
    users, filters = app
    required = rule("deny", category="language")
    filters.set_instance([required])
    entered, release = threading.Event(), threading.Event()
    actual = filter_policy_api._reauthenticate

    def pause(*args):
        answer = actual(*args)
        entered.set()
        assert release.wait(20)
        return answer

    monkeypatch.setattr(filter_policy_api, "_reauthenticate", pause)
    with pending(lambda: policy(filters, USER, [required["id"]]), entered, release) as result:
        users.ok("POST", f"/api/users/{ADMIN}/ownership", OWNER, fresh(OWNER))
    assert code(result["response"]) == (403, "capability_denied")
    assert filters.view(USER)["enforced_rules"] == []
