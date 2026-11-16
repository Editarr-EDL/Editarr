"""MU-14: policy revisions, outdated exports, and read-only views.

Harness: `tests.filter_policy_http_support`. Repository calls run in fresh sessions
on the same database to observe what the HTTP writes committed.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from scanner.db import (
    AuditEventRow,
    MediaItemExportRow,
    MediaItemRow,
    UserFilterEnforcementRow,
    UserFilterRuleOverrideRow,
    UserRow,
)
from scanner.models import FilterDefaultsUpdate, Severity
from scanner.persistence.repository.filter_enforcement import outdated_policy_exports
from scanner.persistence.repository.filter_preferences import (
    effective_filter_settings,
    enforced_filter_policy,
)
from scanner.persistence.repository.repository_settings import (
    get_structured_filter_defaults_settings,
    replace_filter_defaults,
)
from scanner.settings.filter_defaults import apply_filter_defaults, enforce_filter_policy
from scanner.settings.filter_policy.events import FilterPolicyChange, PolicyChangeCause
from tests.filter_policy_http_support import BASE, rule, severities
from tests.filter_policy_http_support import accounts as accounts
from tests.filter_policy_http_support import filter_app as filter_app
from tests.multi_user_access_support import ADMIN, OWNER, PEER_USER, USER
from tests.test_filter_defaults import _filter


def _replace(accounts, rules, **chosen):
    with accounts.factory() as db:
        current = get_structured_filter_defaults_settings(db)
        update = FilterDefaultsUpdate(
            revision=current.revision, selection_rules=rules, severity_defaults=severities(**chosen)
        )
        return replace_filter_defaults(db, update)[1]


def _edited(item: dict, **changes) -> dict:
    return {**item, **changes}


def test_policy_revisions_follow_only_changes_to_the_enforced_view(filter_app, accounts):
    swearing = rule("deny", category="language", subcategory="swearing")
    low = rule("allow", severity="low")
    filter_app.set_instance([swearing, low])
    switched_off = filter_app.live(USER, overrides=[{"rule_id": swearing["id"], "disabled": True}])
    assert switched_off.status_code == 200
    assert filter_app.enforce(OWNER, USER, [swearing["id"]], ["blasphemy"]).status_code == 200
    assert filter_app.enforce(OWNER, PEER_USER, [low["id"]]).status_code == 200

    assert _replace(accounts, [swearing, _edited(low, label_contains="x")]) == [
        FilterPolicyChange(PEER_USER, 2, PolicyChangeCause.INSTANCE_DEFAULTS)
    ]
    assert _replace(accounts, [swearing, _edited(low, label_contains="x")], swearing="high") == []
    assert _replace(
        accounts, [swearing, _edited(low, label_contains="x")], swearing="high", blasphemy="low"
    ) == [FilterPolicyChange(USER, 2, PolicyChangeCause.INSTANCE_DEFAULTS)]
    unchanged = filter_app.enforce(OWNER, USER, [swearing["id"]], ["blasphemy"])
    assert unchanged.json()["policy_revision"] == 2

    assert _replace(accounts, [_edited(low, label_contains="x")], blasphemy="low") == [
        FilterPolicyChange(USER, 3, PolicyChangeCause.INSTANCE_DEFAULTS)
    ]
    with accounts.factory() as db:
        assert db.scalars(select(UserFilterRuleOverrideRow)).all() == []
        enforced = db.scalars(
            select(UserFilterEnforcementRow).order_by(
                UserFilterEnforcementRow.user_id, UserFilterEnforcementRow.target
            )
        )
        assert [(row.user_id, row.target) for row in enforced] == [
            (USER, "blasphemy"),
            (PEER_USER, low["id"]),
        ]
        policy = enforced_filter_policy(db, USER)
        assert (policy.policy_revision, policy.rules, dict(policy.severities)) == (
            3,
            (),
            {"blasphemy": "low"},
        )
    view = filter_app.view(USER)
    assert (view["revision"], view["enforced_rules"]) == (2, [])


def test_instance_edits_audit_each_policy_change_they_cause(filter_app, accounts):
    """USER keeps in sync and PEER_USER has its own copy; each gets one event per change."""
    swearing = rule("deny", category="language", subcategory="swearing")
    low = rule("allow", severity="low")
    filter_app.set_instance([swearing, low])
    assert filter_app.mode(ADMIN, "copy", of=PEER_USER).status_code == 200
    assert filter_app.enforce(OWNER, USER, [swearing["id"]]).status_code == 200
    assert filter_app.enforce(OWNER, PEER_USER, [], ["blasphemy"]).status_code == 200

    filter_app.set_instance([swearing, _edited(low, label_contains="x")])
    filter_app.set_instance([_edited(swearing, label_contains="y"), low], blasphemy="high")

    with accounts.factory() as db:
        events = db.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.action == "filters.policy.change")
            .order_by(AuditEventRow.id)
        ).all()
    instance_driven = [
        (event.actor_id, event.target_user_id, event.outcome, json.loads(event.details_json))
        for event in events
        if json.loads(event.details_json)["cause"] == "instance_defaults"
    ]
    assert instance_driven == [
        (OWNER, USER, "succeeded", {"cause": "instance_defaults", "policy_revision": 2}),
        (OWNER, PEER_USER, "succeeded", {"cause": "instance_defaults", "policy_revision": 2}),
    ]
    assert [filter_app.view(OWNER, user)["policy_revision"] for user in (USER, PEER_USER)] == [2, 2]


def _export(db, export_id, scope, *, target=None, actor, owner, revision):
    target_export = None
    if target is not None:
        target_export = db.scalar(select(UserRow.local_export_id).where(UserRow.id == target))
    db.add(
        MediaItemRow(
            id=f"item-{export_id}",
            server_id="server",
            jellyfin_id=f"item-{export_id}",
            item_type="Movie",
            name=f"Movie {export_id}",
        )
    )
    db.flush()
    db.add(
        MediaItemExportRow(
            id=export_id,
            media_item_id=f"item-{export_id}",
            scope=scope,
            target_user_id=target,
            target_export_id=target_export,
            actor_id=actor,
            policy_owner_id=owner,
            original_author_id=owner,
            policy_revision=revision,
        )
    )


def test_outdated_exports_are_found_by_policy_owner_never_by_target_or_actor(filter_app, accounts):
    swearing = rule("deny", category="language", subcategory="swearing")
    filter_app.set_instance([swearing])
    with accounts.factory() as db:
        _export(db, 1, "personal", target=USER, actor=ADMIN, owner=PEER_USER, revision=0)
        _export(db, 2, "default", actor=USER, owner=USER, revision=0)
        _export(db, 3, "personal", target=PEER_USER, actor=USER, owner=USER, revision=1)
        _export(db, 4, "legacy", actor=USER, owner=USER, revision=None)
        db.commit()

    def outdated(user_id):
        with accounts.factory() as db:
            return [row.id for row in outdated_policy_exports(db, user_id)]

    assert [outdated(user) for user in (USER, PEER_USER, ADMIN)] == [[], [], []]
    assert filter_app.enforce(OWNER, USER, [swearing["id"]]).status_code == 200
    assert [outdated(user) for user in (USER, PEER_USER, ADMIN)] == [[2], [], []]
    assert filter_app.enforce(OWNER, PEER_USER, [swearing["id"]]).status_code == 200
    assert [outdated(user) for user in (USER, PEER_USER, ADMIN)] == [[2], [1], []]


def _review_view(accounts, finding):
    with accounts.factory() as db:
        policy = enforced_filter_policy(db, USER)
    return policy.policy_revision, enforce_filter_policy([finding], policy).filters[0]


def test_personal_severity_defaults_never_change_the_review_view_or_policy_revision(
    filter_app, accounts
):
    deny_high = rule("deny", category="language", severity="high")
    filter_app.set_instance([deny_high], swearing="high")
    assert filter_app.enforce(OWNER, USER, [deny_high["id"]], ["blasphemy"]).status_code == 200
    finding = _filter(severity=Severity.HIGH, selected=False)
    before = _review_view(accounts, finding)

    for severity in ("low", "medium", "high"):
        overrides = [{"subcategory": "swearing", "severity": severity}]
        assert filter_app.live(USER, severity_overrides=overrides).status_code == 200
        assert _review_view(accounts, finding) == before
    assert before[1].selected is True and before[0] == filter_app.view(USER)["policy_revision"]


def test_apply_reads_the_posted_saved_content_never_the_review_view(filter_app, accounts):
    deny_high = rule("deny", category="language", severity="high")
    filter_app.set_instance([deny_high], swearing="low")
    assert filter_app.enforce(OWNER, USER, [deny_high["id"]]).status_code == 200
    saved = _filter(severity=Severity.HIGH, selected=False)
    _, viewed = _review_view(accounts, saved)
    with accounts.factory() as db:
        settings = effective_filter_settings(db, USER)
        policy = enforced_filter_policy(db, USER)

    body = {"filters": [saved.model_dump(mode="json")]}
    applied = filter_app.ok("POST", f"{BASE}/apply", USER, body)["filters"][0]

    expected = apply_filter_defaults([saved], settings).filters[0]
    assert viewed.selected is True
    assert (applied["selected"], applied["severity"]) == (False, "low")
    assert applied == expected.model_dump(mode="json")
    assert apply_filter_defaults([viewed], settings).filters[0].selected is True
    assert enforce_filter_policy([expected], policy).filters[0] == expected


def test_reading_preferences_and_policies_changes_nothing(filter_app, accounts):
    swearing = rule("deny", category="language", subcategory="swearing")
    filter_app.set_instance([swearing])
    assert filter_app.enforce(OWNER, USER, [swearing["id"]], ["swearing"]).status_code == 200
    assert filter_app.mode(PEER_USER, "copy").status_code == 200
    before = filter_app.tables()
    for _ in range(2):
        for user in (OWNER, ADMIN, USER, PEER_USER):
            filter_app.view(user)
        filter_app.view(OWNER, USER)
        filter_app.ok("GET", f"{BASE}/default-mode", USER)
        with accounts.factory() as db:
            effective_filter_settings(db, PEER_USER)
            enforced_filter_policy(db, USER)
    assert filter_app.tables() == before
