"""Every account has its own filter mode, and required items stay in force through mode changes.

Required rules and severities always take the server's current value. Each check
also fills a matching unselected finding from the account's defaults and views it
under the account's enforced policy, so a required Select rule must select it.
Harness: `tests.filter_policy_http_support`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scanner.db import UserRow
from scanner.formats.export_layout import new_local_export_id
from scanner.models import Severity
from scanner.persistence.repository.filter_preferences import (
    effective_filter_settings,
    enforced_filter_policy,
    filter_preferences_view,
    start_preferences,
)
from scanner.settings.filter_defaults import apply_filter_defaults, enforce_filter_policy
from tests.filter_policy_http_support import BASE, code, rule, severities
from tests.filter_policy_http_support import accounts as accounts
from tests.filter_policy_http_support import filter_app as filter_app
from tests.multi_user_access_support import ADMIN, OWNER, PEER_USER, USER
from tests.test_filter_defaults import _filter

NEWCOMER = 6


def _assert_required(filter_app, accounts, user, required, severity):
    view = filter_app.view(OWNER, user)
    enforced = [(e["rule"]["id"], e["rule"]["severity"]) for e in view["rules"] if e["enforced"]]
    assert enforced == [(required["id"], severity)]
    swearing = next(e for e in view["severities"] if e["subcategory"] == "swearing")
    assert (swearing["severity"], swearing["enforced"]) == (severity, True)
    finding = _filter(severity=Severity(severity), selected=False)
    with accounts.factory() as db:
        filled = apply_filter_defaults([finding], effective_filter_settings(db, user))
        viewed = enforce_filter_policy([finding], enforced_filter_policy(db, user))
    assert (filled.filters[0].selected, filled.filters[0].severity) == (True, severity)
    assert viewed.filters[0].selected is True


def test_mode_changes_keep_required_items_at_the_servers_current_value(filter_app, accounts):
    required = rule("deny", category="language", severity="high")
    filter_app.set_instance([required], swearing="high")
    switched_off = filter_app.live(
        USER,
        overrides=[{"rule_id": required["id"], "disabled": True}],
        severity_overrides=[{"subcategory": "swearing", "severity": "low"}],
    )
    assert switched_off.status_code == 200
    assert filter_app.mode(PEER_USER, "copy").status_code == 200
    view = filter_app.view(PEER_USER)
    emptied = {"mode": "copy", "revision": view["revision"], "rules": []}
    assert filter_app.save(PEER_USER, {**emptied, "severities": severities()}).status_code == 200
    for user in (USER, PEER_USER):
        assert filter_app.enforce(OWNER, user, [required["id"]], ["swearing"]).status_code == 200
        _assert_required(filter_app, accounts, user, required, "high")

    assert code(filter_app.mode(USER, "copy")) == (409, "filter_policy_enforced")
    assert code(filter_app.mode(PEER_USER, "live")) == (409, "filter_policy_enforced")
    assert filter_app.mode(ADMIN, "copy", of=USER).status_code == 200
    assert filter_app.mode(ADMIN, "live", of=PEER_USER).status_code == 200
    for user in (USER, PEER_USER):
        _assert_required(filter_app, accounts, user, required, "high")

    edited = {**required, "severity": "medium"}
    filter_app.set_instance([edited], swearing="medium")
    for user in (USER, PEER_USER):
        _assert_required(filter_app, accounts, user, edited, "medium")

    assert filter_app.mode(ADMIN, "live", of=USER).status_code == 200
    assert filter_app.mode(ADMIN, "copy", of=PEER_USER).status_code == 200
    for user in (USER, PEER_USER):
        _assert_required(filter_app, accounts, user, edited, "medium")
    assert [filter_app.view(OWNER, user)["mode"] for user in (USER, PEER_USER)] == ["live", "copy"]


def test_the_starting_mode_applies_only_to_accounts_created_after_it(filter_app, accounts):
    required = rule("deny", category="language", severity="high")
    filter_app.set_instance([required], swearing="high")
    assert filter_app.mode(ADMIN, "copy", of=PEER_USER).status_code == 200
    assert filter_app.enforce(OWNER, USER, [required["id"]], ["swearing"]).status_code == 200
    before = {user: filter_app.view(OWNER, user) for user in (OWNER, ADMIN, USER, PEER_USER)}

    for starting in ("copy", "live", "copy"):
        assert filter_app.ok("PUT", f"{BASE}/default-mode", ADMIN, {"mode": starting}) == {
            "mode": starting
        }
        assert {user: filter_app.view(OWNER, user) for user in before} == before
    _assert_required(filter_app, accounts, USER, required, "high")

    with accounts.factory() as db:
        db.add(
            UserRow(
                id=NEWCOMER,
                role="user",
                status="active",
                username="newcomer",
                normalized_username="newcomer",
                local_export_id=new_local_export_id().value,
            )
        )
        db.flush()
        start_preferences(db, NEWCOMER, datetime.now(timezone.utc))
        db.commit()
        started = filter_preferences_view(db, NEWCOMER)
    assert (started.mode, started.revision, started.policy_revision) == ("copy", 0, 0)
    assert [(e.rule.id, e.origin) for e in started.rules] == [(required["id"], "copy")]

    filter_app.set_instance([], swearing="low")
    with accounts.factory() as db:
        assert filter_preferences_view(db, NEWCOMER).settings() == started.settings()
    assert filter_app.view(OWNER, ADMIN)["rules"] == []
