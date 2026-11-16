"""MU-14: the pure effective-policy evaluator, table-driven.

Covers live inheritance with overrides and personal rules, independent copies,
enforcement in both modes, overlapping allow/deny rules, severity locks, and the
effective view of saved review filters, which never changes its inputs.
"""

import copy

import pytest

from scanner.models import (
    FilterDefaultsSettings,
    FilterRuleDisposition,
    McfCategory,
    McfSeverityDefault,
    Severity,
)
from scanner.settings.filter_defaults import (
    EnforcedFilterPolicy,
    apply_filter_defaults,
    enforce_filter_policy,
)
from scanner.settings.filter_policy.effective import enforced_policy, live_copy, preferences_view
from scanner.settings.filter_policy.values import (
    CopyValues,
    Enforcement,
    PersonalFilterValues,
    RuleOverride,
)
from tests.test_filter_defaults import _filter, _rule

ALLOW, DENY = FilterRuleDisposition.ALLOW, FilterRuleDisposition.DENY
SWEARING, BLASPHEMY, KISSING = McfCategory.swearing, McfCategory.blasphemy, McfCategory.kissing


def _instance(rules, revision=1, **severities):
    return FilterDefaultsSettings(
        revision=revision,
        selection_rules=rules,
        severity_defaults=[
            McfSeverityDefault(
                subcategory=subcategory, severity=severities.get(subcategory.value, Severity.LOW)
            )
            for subcategory in McfCategory
        ],
    )


def _severity(view, subcategory):
    return next(entry for entry in view.severities if entry.subcategory == subcategory)


DENY_SWEARING = _rule(DENY, category="language", subcategory=SWEARING)
ALLOW_LOW = _rule(ALLOW, severity=Severity.LOW)
DENY_HIGH = _rule(DENY, severity=Severity.HIGH)
PERSONAL = _rule(DENY, category="sex", subcategory=KISSING)


def test_a_live_user_inherits_instance_changes_except_overridden_fields():
    edited = DENY_HIGH.model_copy(update={"severity": Severity.MEDIUM})
    values = PersonalFilterValues(
        user_id=4,
        mode="live",
        overrides={
            ALLOW_LOW.id: RuleOverride(ALLOW_LOW.id, disabled=True),
            DENY_HIGH.id: RuleOverride(DENY_HIGH.id, disabled=False, rule=edited),
        },
        personal_rules=(PERSONAL,),
        severity_overrides={BLASPHEMY: Severity.HIGH},
    )
    renamed = DENY_SWEARING.model_copy(update={"label_contains": "changed"})
    added = _rule(ALLOW, category="drugs")
    before = _instance([DENY_SWEARING, ALLOW_LOW, DENY_HIGH])
    after = _instance(
        [renamed, ALLOW_LOW, DENY_HIGH.model_copy(update={"evidence_source": "audio"}), added],
        revision=2,
        swearing=Severity.MEDIUM,
        blasphemy=Severity.MEDIUM,
    )

    view = preferences_view(after, values)

    assert [(e.rule, e.origin, e.edited, e.disabled) for e in view.rules] == [
        (renamed, "instance", False, False),
        (ALLOW_LOW, "instance", False, True),
        (edited, "instance", True, False),
        (added, "instance", False, False),
        (PERSONAL, "personal", False, False),
    ]
    assert _severity(view, SWEARING).severity is Severity.MEDIUM
    assert (_severity(view, BLASPHEMY).severity, _severity(view, BLASPHEMY).origin) == (
        Severity.HIGH,
        "override",
    )
    assert view.settings().selection_rules == [renamed, edited, added, PERSONAL]
    assert preferences_view(before, values).instance_revision == 1


def test_a_copy_never_follows_instance_changes():
    instance = _instance([DENY_SWEARING])
    values = PersonalFilterValues(
        user_id=4,
        mode="copy",
        copy=live_copy(instance, PersonalFilterValues(user_id=4, mode="live")),
    )
    changed = _instance([DENY_SWEARING.model_copy(update={"disposition": ALLOW})], swearing="high")

    view = preferences_view(changed, values)

    assert view.mode == "copy"
    assert [(e.rule, e.origin) for e in view.rules] == [(DENY_SWEARING, "copy")]
    assert _severity(view, SWEARING).severity is Severity.LOW


def test_entering_copy_mode_starts_from_what_the_user_saw_live():
    instance = _instance([DENY_SWEARING, ALLOW_LOW])
    values = PersonalFilterValues(
        user_id=4,
        mode="live",
        overrides={ALLOW_LOW.id: RuleOverride(ALLOW_LOW.id, disabled=True)},
        personal_rules=(PERSONAL,),
        severity_overrides={SWEARING: Severity.HIGH},
    )
    taken = live_copy(instance, values)
    assert taken.rules == (DENY_SWEARING, PERSONAL)
    assert taken.severities[SWEARING] is Severity.HIGH
    assert taken.severities[BLASPHEMY] is Severity.LOW


@pytest.mark.parametrize("mode", ["live", "copy"])
def test_enforced_rules_and_severities_win_in_either_mode(mode):
    instance = _instance([DENY_SWEARING, ALLOW_LOW], swearing="high")
    hidden = DENY_SWEARING.model_copy(update={"disposition": ALLOW})
    values = PersonalFilterValues(
        user_id=4,
        mode=mode,
        overrides={DENY_SWEARING.id: RuleOverride(DENY_SWEARING.id, disabled=True, rule=hidden)},
        severity_overrides={SWEARING: Severity.LOW},
        copy=CopyValues(rules=(hidden, ALLOW_LOW), severities={SWEARING: Severity.LOW}),
        enforcement=Enforcement(frozenset({DENY_SWEARING.id}), frozenset({SWEARING})),
    )

    view = preferences_view(instance, values)

    enforced = [entry for entry in view.rules if entry.enforced]
    assert [(entry.rule, entry.disabled) for entry in enforced] == [(DENY_SWEARING, False)]
    assert hidden not in view.settings().selection_rules
    assert (_severity(view, SWEARING).severity, _severity(view, SWEARING).enforced) == (
        Severity.HIGH,
        True,
    )
    assert view.mode_locked
    assert view.enforced_rules == [DENY_SWEARING.id] and view.enforced_severities == [SWEARING]
    policy = enforced_policy(instance, values)
    assert policy.rules == (DENY_SWEARING,) and dict(policy.severities) == {SWEARING: Severity.HIGH}


def test_the_enforced_policy_does_not_depend_on_mode_or_personal_values():
    instance = _instance([DENY_SWEARING, ALLOW_LOW], swearing="high")
    enforcement = Enforcement(frozenset({DENY_SWEARING.id}), frozenset({SWEARING}))
    plain = PersonalFilterValues(user_id=4, mode="live", enforcement=enforcement)
    busy = PersonalFilterValues(
        user_id=4,
        mode="copy",
        personal_rules=(PERSONAL,),
        severity_overrides={SWEARING: Severity.LOW},
        copy=CopyValues(rules=(), severities={}),
        enforcement=enforcement,
    )
    assert enforced_policy(instance, plain) == enforced_policy(instance, busy)


def _policy(*rules, **severities):
    return EnforcedFilterPolicy(
        policy_revision=3,
        rules=rules,
        severities={McfCategory(name): Severity(value) for name, value in severities.items()},
    )


CASES = [
    # (saved filter, policy, effective selected, effective severity)
    (dict(selected=False), _policy(DENY_SWEARING), True, Severity.MEDIUM),
    (dict(selected=True), _policy(_rule(ALLOW, category="language")), True, Severity.MEDIUM),
    (dict(selected=False), _policy(DENY_SWEARING, _rule(ALLOW, severity="medium")), True, None),
    (dict(selected=True), _policy(_rule(DENY, category="sex")), True, Severity.MEDIUM),
    (dict(selected=False), _policy(_rule(DENY, category="sex")), False, Severity.MEDIUM),
    (dict(severity=Severity.LOW), _policy(swearing="high"), True, Severity.HIGH),
    (dict(severity=Severity.LOW, selected=False), _policy(DENY_HIGH, swearing="high"), True, None),
    (dict(severity=Severity.HIGH, selected=True), _policy(ALLOW_LOW, swearing="low"), True, None),
    (dict(severity=Severity.HIGH, selected=False), _policy(ALLOW_LOW, swearing="low"), False, None),
    (dict(subcategory=BLASPHEMY), _policy(swearing="high"), True, Severity.MEDIUM),
]


@pytest.mark.parametrize(("saved", "policy", "selected", "severity"), CASES)
def test_the_effective_view_of_saved_filters(saved, policy, selected, severity):
    filters = [_filter(**saved)]
    effective = enforce_filter_policy(filters, policy).filters[0]
    assert effective.selected is selected
    expected = severity or policy.severities.get(filters[0].subcategory, filters[0].severity)
    assert effective.severity is expected
    assert effective.mcf_channel == filters[0].mcf_channel


def test_repeated_evaluation_is_deterministic_and_never_changes_inputs():
    filters = [
        _filter(selected=False),
        _filter(subcategory=BLASPHEMY, severity=Severity.LOW),
        _filter(severity=Severity.HIGH, selected=True),
    ]
    policy = _policy(DENY_SWEARING, ALLOW_LOW, swearing="high", blasphemy="low")
    saved = copy.deepcopy(filters)
    snapshot = copy.deepcopy(policy)

    first = enforce_filter_policy(filters, policy)
    second = enforce_filter_policy(filters, policy)

    assert first == second
    assert (first.selection_changes, first.severity_changes) == (1, 1)
    assert filters == saved and policy == snapshot
    assert all(a is not b for a, b in zip(first.filters, filters, strict=True))


@pytest.mark.parametrize(
    ("inherited", "personal"),
    [(DENY_SWEARING, ALLOW_LOW), (ALLOW_LOW, DENY_SWEARING)],
    ids=["inherited-deny", "personal-deny"],
)
def test_deny_wins_between_inherited_and_personal_rules_regardless_of_order(inherited, personal):
    values = PersonalFilterValues(user_id=4, mode="live", personal_rules=(personal,))
    settings = preferences_view(_instance([inherited]), values).settings()
    assert settings.selection_rules == [inherited, personal]
    finding = _filter(selected=False, severity=Severity.LOW)
    assert apply_filter_defaults([finding], settings).filters[0].selected is True


def test_filling_with_effective_settings_applies_the_enforced_values():
    instance = _instance([DENY_SWEARING], swearing="high")
    values = PersonalFilterValues(
        user_id=4,
        mode="live",
        overrides={DENY_SWEARING.id: RuleOverride(DENY_SWEARING.id, disabled=True)},
        severity_overrides={SWEARING: Severity.LOW},
        enforcement=Enforcement(frozenset({DENY_SWEARING.id}), frozenset({SWEARING})),
    )
    settings = preferences_view(instance, values).settings()
    result = apply_filter_defaults([_filter(selected=False, severity=Severity.LOW)], settings)
    assert (result.filters[0].selected, result.filters[0].severity) == (True, Severity.HIGH)
