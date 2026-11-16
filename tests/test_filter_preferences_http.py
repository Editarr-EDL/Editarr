"""MU-14 acceptance through HTTP: live inheritance, copies, enforcement, and authority.

Harness: `tests.filter_policy_http_support` (live accounts owner 1, admins 2 and 3,
users 4 and 5, verification mode, real filter routes).
"""

from __future__ import annotations

import json

from sqlalchemy import select

from scanner.db import AuditEventRow, UserFilterRuleOverrideRow
from tests.filter_policy_http_support import BASE, code, rule, severities
from tests.filter_policy_http_support import accounts as accounts
from tests.filter_policy_http_support import filter_app as filter_app
from tests.multi_user_access_support import ADMIN, OWNER, PEER_ADMIN, PEER_USER, USER


def _rules(view: dict) -> list[tuple]:
    return [
        (e["rule"]["id"], e["rule"].get("label_contains"), e["origin"], e["edited"], e["disabled"])
        for e in view["rules"]
    ]


def _severity(view: dict, subcategory: str) -> tuple[str, str, bool]:
    entry = next(e for e in view["severities"] if e["subcategory"] == subcategory)
    return entry["severity"], entry["origin"], entry["enforced"]


def _edited(item: dict, **changes) -> dict:
    return {**item, **changes}


def test_live_users_inherit_owner_edits_except_overrides_and_copies_stay_independent(filter_app):
    swearing = rule("deny", category="language", subcategory="swearing", label_contains="a")
    low = rule("allow", severity="low", label_contains="b")
    filter_app.set_instance([swearing, low])
    personal = rule("deny", category="sex", subcategory="kissing")
    pinned = _edited(low, label_contains="mine")
    saved = filter_app.live(
        USER,
        overrides=[{"rule_id": low["id"], "rule": pinned}],
        personal_rules=[personal],
        severity_overrides=[{"subcategory": "blasphemy", "severity": "high"}],
    )
    assert saved.status_code == 200, saved.text
    assert filter_app.mode(PEER_USER, "copy").status_code == 200

    added = rule("allow", category="drugs")
    filter_app.set_instance(
        [_edited(swearing, label_contains="new"), _edited(low, label_contains="owner"), added],
        swearing="high",
        blasphemy="low",
    )

    live = filter_app.view(USER)
    assert _rules(live) == [
        (swearing["id"], "new", "instance", False, False),
        (low["id"], "mine", "instance", True, False),
        (added["id"], None, "instance", False, False),
        (personal["id"], None, "personal", False, False),
    ]
    assert _severity(live, "swearing") == ("high", "instance", False)
    assert _severity(live, "blasphemy") == ("high", "override", False)
    copy = filter_app.view(PEER_USER)
    assert copy["mode"] == "copy"
    assert _rules(copy) == [
        (swearing["id"], "a", "copy", False, False),
        (low["id"], "b", "copy", False, False),
    ]
    assert _severity(copy, "swearing") == ("medium", "copy", False)

    reset = filter_app.live(USER, personal_rules=[personal])
    assert reset.status_code == 200
    assert _rules(reset.json())[1] == (low["id"], "owner", "instance", False, False)
    assert _severity(reset.json(), "blasphemy") == ("low", "instance", False)


def test_enforced_rules_and_severities_win_while_the_rest_stays_editable(filter_app, accounts):
    swearing = rule("deny", category="language", subcategory="swearing")
    low = rule("allow", severity="low")
    filter_app.set_instance([swearing, low], swearing="high")
    switched_off = filter_app.live(
        USER,
        overrides=[{"rule_id": swearing["id"], "disabled": True}],
        severity_overrides=[{"subcategory": "swearing", "severity": "low"}],
    )
    assert switched_off.status_code == 200

    enforced = filter_app.enforce(OWNER, USER, [swearing["id"]], ["swearing"])
    assert enforced.status_code == 200, enforced.text
    view = enforced.json()
    assert (view["policy_revision"], view["mode_locked"]) == (1, True)
    assert [(e["rule"]["id"], e["enforced"], e["disabled"]) for e in view["rules"]][0] == (
        swearing["id"],
        True,
        False,
    )
    assert _severity(view, "swearing") == ("high", "instance", True)
    with accounts.factory() as db:
        beneath = db.scalars(select(UserFilterRuleOverrideRow)).all()
        assert [(row.user_id, row.rule_id, row.disabled) for row in beneath] == [
            (USER, swearing["id"], True)
        ]

    refused = filter_app.live(USER, overrides=[{"rule_id": swearing["id"], "disabled": True}])
    assert code(refused) == (409, "filter_policy_enforced")
    refused = filter_app.live(
        USER, severity_overrides=[{"subcategory": "swearing", "severity": "low"}]
    )
    assert code(refused) == (409, "filter_policy_enforced")
    allowed = filter_app.live(USER, overrides=[{"rule_id": low["id"], "disabled": True}])
    assert allowed.status_code == 200
    assert code(filter_app.mode(USER, "copy")) == (409, "filter_policy_enforced")

    assert filter_app.mode(ADMIN, "copy", of=USER).status_code == 200
    copied = filter_app.view(USER)
    assert copied["mode"] == "copy"
    assert [(e["rule"]["id"], e["origin"], e["enforced"]) for e in copied["rules"]] == [
        (swearing["id"], "instance", True)
    ]
    assert _severity(copied, "swearing") == ("high", "instance", True)
    body = {"mode": "copy", "revision": copied["revision"], "rules": [swearing]}
    body["severities"] = [s for s in severities() if s["subcategory"] != "swearing"]
    assert code(filter_app.save(USER, body)) == (409, "filter_policy_enforced")
    body["rules"] = [low]
    assert code(filter_app.save(USER, {**body, "severities": severities()})) == (
        409,
        "filter_policy_enforced",
    )
    assert code(filter_app.save(USER, {**body, "severities": body["severities"][1:]})) == (
        422,
        "filter_severities_incomplete",
    )
    assert filter_app.save(USER, body).status_code == 200

    lifted = filter_app.enforce(OWNER, USER)
    assert lifted.status_code == 200
    restored = lifted.json()
    assert restored["policy_revision"] == 2 and not restored["mode_locked"]
    assert [e["rule"]["id"] for e in restored["rules"]] == [low["id"]]
    assert _severity(restored, "swearing") == ("low", "copy", False)


def test_only_the_owner_edits_instance_values_and_enforces_policy(filter_app, accounts):
    swearing = rule("deny", category="language", subcategory="swearing")
    instance = filter_app.set_instance([swearing])
    change = {"revision": instance["revision"], "selection_rules": []}
    change["severity_defaults"] = severities()
    for user in (ADMIN, USER):
        assert code(filter_app.send("PUT", BASE, user, change)) == (403, "capability_denied")
    refusals = [filter_app.enforce(user, PEER_USER, [swearing["id"]]) for user in (ADMIN, USER)]
    assert [code(refused) for refused in refusals] == [
        (403, "capability_denied"),
        (404, "not_found"),
    ]
    assert code(filter_app.enforce(OWNER, USER, [swearing["id"]], password=False)) == (
        403,
        "reauthentication_required",
    )
    wrong_body = {"policy_revision": 0, "rules": [], "reauthentication": {"password": "x" * 20}}
    wrong = filter_app.send("PUT", f"{BASE}/users/{USER}/policy", OWNER, wrong_body)
    assert code(wrong) == (403, "reauthentication_failed")
    by_key = filter_app.enforce(OWNER, USER, [swearing["id"]], key=True)
    assert code(by_key) == (403, "interactive_session_required")
    assert code(filter_app.enforce(OWNER, OWNER, [swearing["id"]])) == (
        409,
        "owner_filter_policy",
    )
    assert filter_app.view(OWNER, USER)["policy_revision"] == 0

    assert filter_app.mode(ADMIN, "copy", of=USER).status_code == 200
    assert code(filter_app.mode(ADMIN, "copy", of=PEER_ADMIN)) == (403, "protected_account")
    assert code(filter_app.mode(USER, "copy", of=PEER_USER)) == (404, "not_found")
    assert code(filter_app.live(ADMIN, of=PEER_USER)) == (403, "capability_denied")
    assert filter_app.live(OWNER, of=PEER_USER).status_code == 200
    assert code(filter_app.send("GET", f"{BASE}/users/{PEER_USER}", USER)) == (
        404,
        "not_found",
    )
    assert filter_app.view(ADMIN, PEER_USER)["user_id"] == PEER_USER
    default = f"{BASE}/default-mode"
    assert code(filter_app.send("PUT", default, USER, {"mode": "copy"})) == (
        403,
        "capability_denied",
    )
    assert filter_app.ok("PUT", default, ADMIN, {"mode": "copy"}) == {"mode": "copy"}
    assert filter_app.ok("GET", default, USER) == {"mode": "copy"}

    assert filter_app.enforce(OWNER, USER, [swearing["id"]]).status_code == 200
    with accounts.factory() as db:
        events = db.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.action == "filters.policy.change")
            .order_by(AuditEventRow.id)
        ).all()
    assert [(e.actor_id, e.target_user_id, e.outcome) for e in events] == [
        (ADMIN, PEER_USER, "denied"),
        (USER, PEER_USER, "denied"),
        (OWNER, USER, "denied"),
        (OWNER, USER, "failed"),
        (OWNER, USER, "succeeded"),
    ]
    assert json.loads(events[-1].details_json) == {
        "cause": "enforcement",
        "policy_revision": 1,
        "rules": 1,
        "severities": 0,
    }
    assert json.loads(events[1].details_json) == {"reason": "not_found"}


def test_stale_and_inconsistent_writes_are_refused(filter_app):
    swearing = rule("deny", category="language", subcategory="swearing")
    filter_app.set_instance([swearing])
    stale = filter_app.save(USER, {"mode": "live", "revision": 7})
    assert code(stale) == (409, "stale_settings_revision")
    wrong_mode = filter_app.save(
        USER, {"mode": "copy", "revision": 0, "rules": [], "severities": severities()}
    )
    assert code(wrong_mode) == (409, "filter_mode_changed")
    unknown = filter_app.live(USER, overrides=[{"rule_id": rule("deny")["id"], "disabled": True}])
    assert code(unknown) == (409, "unknown_filter_rule")
    clash = filter_app.live(USER, personal_rules=[_edited(swearing, disposition="allow")])
    assert code(clash) == (409, "filter_rule_id_conflict")

    personal = rule("deny", category="sex")
    assert filter_app.live(USER, personal_rules=[personal]).status_code == 200
    instance = filter_app.instance()
    change = {"revision": instance["revision"], "selection_rules": [swearing, personal]}
    change["severity_defaults"] = severities()
    assert code(filter_app.send("PUT", BASE, OWNER, change)) == (409, "filter_rule_id_conflict")
    assert filter_app.instance()["revision"] == instance["revision"]

    first = filter_app.view(USER)["revision"]
    assert filter_app.live(USER).status_code == 200
    replay = filter_app.save(USER, {"mode": "live", "revision": first})
    assert code(replay) == (409, "stale_settings_revision")
