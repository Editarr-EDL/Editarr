"""MU-14: fresh authentication for instance edits, serialized writers, and old orphans.

An instance edit that changes any user's enforced policy needs the owner's password
in that request; API keys never qualify. Writers take the write lock before their
first read, so races are injected deterministically just before that lock, through
separate HTTP requests, and the writer decides on what they committed.
Harness: `tests.filter_policy_http_support`.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from scanner.db import (
    AuditEventRow,
    UserFilterEnforcementRow,
    UserFilterPersonalRuleRow,
    UserFilterRuleOverrideRow,
)
from scanner.persistence.repository import filter_preferences
from scanner.settings.filter_defaults import apply_filter_defaults
from tests.filter_policy_http_support import BASE, code, rule, severities, takes_write_lock
from tests.filter_policy_http_support import accounts as accounts
from tests.filter_policy_http_support import before_statement as before_statement
from tests.filter_policy_http_support import filter_app as filter_app
from tests.multi_user_access_support import ADMIN, OWNER, OWNER_PASSWORD, USER
from tests.test_filter_defaults import _filter


def _instance_body(filter_app, rules, password=None, **chosen) -> dict:
    body = {
        "revision": filter_app.instance()["revision"],
        "selection_rules": rules,
        "severity_defaults": severities(**chosen),
    }
    if password is not None:
        body["reauthentication"] = {"password": password}
    return body


def _policy_events(accounts) -> list[tuple]:
    with accounts.factory() as db:
        events = db.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.action == "filters.policy.change")
            .order_by(AuditEventRow.id)
        ).all()
    return [
        (event.target_user_id, event.outcome, json.loads(event.details_json)) for event in events
    ]


@pytest.mark.parametrize("mode", ["live", "copy"])
def test_instance_edits_that_change_enforced_policy_need_fresh_authentication(
    filter_app, accounts, mode
):
    required = rule("deny", category="language")
    other = rule("allow", category="sex")
    filter_app.set_instance([required, other])
    assert filter_app.mode(ADMIN, mode, of=USER).status_code == 200
    assert filter_app.enforce(OWNER, USER, [required["id"]], ["swearing"]).status_code == 200
    instance, view, events = filter_app.instance(), filter_app.view(USER), _policy_events(accounts)

    deleted = _instance_body(filter_app, [other])
    by_key = filter_app.send("PUT", BASE, OWNER, deleted, key=True)
    assert code(by_key) == (403, "interactive_session_required")
    keyed_password = _instance_body(filter_app, [other], OWNER_PASSWORD)
    assert code(filter_app.send("PUT", BASE, OWNER, keyed_password, key=True))[1] == (
        "interactive_session_required"
    )
    severity = _instance_body(filter_app, [required, other], swearing="low")
    assert code(filter_app.send("PUT", BASE, OWNER, severity)) == (
        403,
        "reauthentication_required",
    )
    wrong = _instance_body(filter_app, [other], "not the password")
    assert code(filter_app.send("PUT", BASE, OWNER, wrong))[1] == "reauthentication_failed"
    assert (filter_app.instance(), filter_app.view(USER)) == (instance, view)
    refusals = _policy_events(accounts)[len(events) :]
    assert [(target, outcome, details["reason"]) for target, outcome, details in refusals] == [
        (USER, "denied", "interactive_session_required"),
        (USER, "denied", "interactive_session_required"),
        (USER, "denied", "reauthentication_required"),
        (USER, "failed", "reauthentication_failed"),
    ]

    unrelated = _instance_body(filter_app, [required, {**other, "label_contains": "x"}])
    assert filter_app.send("PUT", BASE, OWNER, unrelated, key=True).status_code == 200
    assert filter_app.view(USER)["policy_revision"] == view["policy_revision"]

    accepted = _instance_body(filter_app, [other], OWNER_PASSWORD)
    assert filter_app.send("PUT", BASE, OWNER, accepted).status_code == 200
    after = filter_app.view(USER)
    assert (after["enforced_rules"], after["policy_revision"]) == ([], view["policy_revision"] + 1)
    assert _policy_events(accounts)[-1] == (
        USER,
        "succeeded",
        {"cause": "instance_defaults", "policy_revision": view["policy_revision"] + 1},
    )


def _stored(accounts, table) -> list:
    with accounts.factory() as db:
        return db.scalars(select(table)).all()


def test_an_instance_deletion_racing_a_personal_save_refuses_the_save(
    filter_app, accounts, before_statement
):
    inherited = rule("deny", category="language")
    filter_app.set_instance([inherited])
    before_statement(takes_write_lock, lambda: filter_app.set_instance([]))

    saved = filter_app.live(USER, overrides=[{"rule_id": inherited["id"], "disabled": True}])

    assert code(saved) == (409, "unknown_filter_rule")
    assert _stored(accounts, UserFilterRuleOverrideRow) == []


def test_an_instance_addition_racing_a_personal_save_refuses_the_duplicate_id(
    filter_app, accounts, before_statement
):
    personal = rule("deny", category="language")
    before_statement(takes_write_lock, lambda: filter_app.set_instance([personal]))

    saved = filter_app.live(USER, personal_rules=[personal])

    assert code(saved) == (409, "filter_rule_id_conflict")
    assert _stored(accounts, UserFilterPersonalRuleRow) == []
    ids = [item["rule"]["id"] for item in filter_app.view(USER)["rules"]]
    assert ids == [personal["id"]]


def test_enforcement_racing_a_personal_save_refuses_the_save(
    filter_app, accounts, before_statement
):
    inherited = rule("deny", category="language")
    filter_app.set_instance([inherited])
    before_statement(takes_write_lock, lambda: filter_app.enforce(OWNER, USER, [inherited["id"]]))

    saved = filter_app.live(USER, overrides=[{"rule_id": inherited["id"], "disabled": True}])

    assert code(saved) == (409, "filter_policy_enforced")
    assert _stored(accounts, UserFilterRuleOverrideRow) == []
    assert filter_app.view(USER)["enforced_rules"] == [inherited["id"]]


def test_an_instance_deletion_racing_enforcement_refuses_the_enforcement(
    filter_app, accounts, before_statement
):
    inherited = rule("deny", category="language")
    filter_app.set_instance([inherited])
    raced = before_statement(takes_write_lock, lambda: filter_app.set_instance([]))

    enforced = filter_app.enforce(OWNER, USER, [inherited["id"]])

    assert raced["left"] == 0
    assert code(enforced) == (409, "unknown_filter_rule")
    with accounts.factory() as db:
        assert db.scalars(select(UserFilterEnforcementRow)).all() == []
    assert not filter_app.view(USER)["mode_locked"]


def test_orphans_left_by_older_data_never_break_the_view_or_apply(filter_app, accounts):
    inherited = rule("deny", category="language")
    filter_app.set_instance([inherited])
    assert filter_app.live(USER, personal_rules=[rule("deny", category="sex")]).status_code == 200
    gone = rule("allow", category="drugs")
    with accounts.factory() as db:
        db.add(UserFilterRuleOverrideRow(user_id=USER, rule_id=gone["id"], disabled=True))
        db.add(
            UserFilterPersonalRuleRow(
                user_id=USER, id=inherited["id"], position=5, disposition="allow", category="sex"
            )
        )
        db.commit()

    ids = [item["rule"]["id"] for item in filter_app.view(USER)["rules"]]
    assert len(ids) == len(set(ids)) and inherited["id"] in ids
    with accounts.factory() as db:
        settings = filter_preferences.effective_filter_settings(db, USER)
    assert apply_filter_defaults([_filter(selected=False)], settings).filters[0].selected

    filter_app.set_instance([inherited, gone])
    rules = filter_app.view(USER)["rules"]
    revived = next(item for item in rules if item["rule"]["id"] == gone["id"])
    assert (revived["disabled"], revived["edited"]) == (False, False)
