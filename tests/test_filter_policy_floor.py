"""MU-14: enforcement is a floor, and the review view of an Applied result is that result.

Covers every combination of the enforced rule's disposition, the user's own or
inherited rule, an override of the enforced rule, the saved selection, the mode,
and how the saved severity and channel relate to the enforced severity and to the
values Apply derives. The view reads saved content and the enforced policy only,
never the owner's non-enforced defaults. Apply reads saved content, never the
view's output, so only view(Apply(saved)) == Apply(saved) is promised. A matching
enforced Deny always filters; an enforced Allow never stops the user's own Deny.
Both rules carry the same match clauses.
"""

import itertools
from dataclasses import replace

import pytest

from scanner.models import FilterRuleDisposition, McfChannel, Severity
from scanner.persistence.repository.review_policy_evidence import covers_evidence
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
from tests.test_filter_policy_engine import SWEARING, _instance

ALLOW, DENY = FilterRuleDisposition.ALLOW, FilterRuleDisposition.DENY
OPPOSITE = {ALLOW: DENY, DENY: ALLOW}
HIGH, MEDIUM, LOW = Severity.HIGH, Severity.MEDIUM, Severity.LOW
AUDIO, VIDEO = McfChannel.AUDIO, McfChannel.VIDEO


@pytest.mark.parametrize(("source", "channel"), [("audio", AUDIO), ("visual", VIDEO)])
def test_implicit_channel_matches_enforcement_and_covers_only_its_source(source, channel):
    finding = _filter(source=source, channel=None, selected=False)
    rule = _rule(DENY, mcf_channel=channel)
    policy = EnforcedFilterPolicy(1, (rule,))
    assert enforce_filter_policy([finding], policy).filters[0].selected
    assert covers_evidence(finding, finding.model_copy(update={"mcf_channel": channel}))
    required_both = finding.model_copy(update={"mcf_channel": McfChannel.BOTH})
    assert not covers_evidence(finding, required_both)
    assert covers_evidence(required_both, finding)


# name: (rule clauses, saved finding, instance swearing, personal swearing,
#        swearing enforced, rules match in the view, rules match after Apply)
DRIFTS = {
    "same": ({}, {}, LOW, None, False, True, True),
    "severity-away": ({"severity": HIGH}, {"severity": HIGH}, LOW, None, False, True, False),
    "severity-toward": ({"severity": HIGH}, {"severity": LOW}, HIGH, None, False, False, True),
    "severity-personal": ({"severity": HIGH}, {"severity": HIGH}, HIGH, LOW, False, True, False),
    "enforced-toward": ({"severity": HIGH}, {"severity": LOW}, HIGH, LOW, True, True, True),
    "enforced-away": ({"severity": HIGH}, {"severity": HIGH}, LOW, HIGH, True, False, False),
    "channel-away": ({"mcf_channel": VIDEO}, {"channel": VIDEO}, LOW, None, False, True, False),
    "channel-toward": ({"mcf_channel": AUDIO}, {"channel": VIDEO}, LOW, None, False, False, True),
}

COMBINATIONS = list(
    itertools.product(
        [DENY, ALLOW],
        ["personal", "inherited"],
        [DENY, ALLOW],
        [None, "disabled", "flipped"],
        [True, False],
        ["live", "copy"],
        DRIFTS,
    )
)


def _combination_id(combination):
    enforced, origin, own, override, saved, mode, drift = combination
    parts = ("enforced", enforced.value, origin, own.value, f"override-{override}")
    return "-".join((*parts, f"saved-{saved}", mode, drift))


def _user(enforced, origin, own, override, mode, drift):
    """The instance and the user's values, with the enforced rule overridden as given."""
    _clauses, _saved, swearing, personal_swearing, severity_enforced, *_ = DRIFTS[drift]
    rules = [enforced, own] if origin == "inherited" else [enforced]
    instance = _instance(rules, swearing=swearing)
    overrides = {}
    if override == "disabled":
        overrides = {enforced.id: RuleOverride(enforced.id, disabled=True)}
    elif override == "flipped":
        flipped = enforced.model_copy(update={"disposition": OPPOSITE[enforced.disposition]})
        overrides = {enforced.id: RuleOverride(enforced.id, disabled=False, rule=flipped)}
    live = PersonalFilterValues(
        user_id=4,
        mode="live",
        overrides=overrides,
        personal_rules=(own,) if origin == "personal" else (),
        severity_overrides={SWEARING: personal_swearing} if personal_swearing else {},
    )
    if mode == "copy":
        live = replace(live, mode="copy", copy=live_copy(instance, live))
    subcategories = frozenset({SWEARING}) if severity_enforced else frozenset()
    return instance, replace(live, enforcement=Enforcement(frozenset({enforced.id}), subcategories))


def _other_personal_defaults(values):
    """The same values with every non-enforced personal severity default changed."""
    copy = values.copy and CopyValues(rules=values.copy.rules, severities={SWEARING: MEDIUM})
    return replace(values, severity_overrides={SWEARING: MEDIUM}, copy=copy)


@pytest.mark.parametrize(
    ("enforced_disposition", "origin", "own_disposition", "override", "saved", "mode", "drift"),
    COMBINATIONS,
    ids=[_combination_id(combination) for combination in COMBINATIONS],
)
def test_the_view_of_apply_is_apply_and_never_goes_below_the_floor(
    enforced_disposition, origin, own_disposition, override, saved, mode, drift
):
    clauses, saved_fields, swearing, _, severity_enforced, in_view, after_apply = DRIFTS[drift]
    enforced = _rule(enforced_disposition, category="language", **clauses)
    own = _rule(own_disposition, category="language", subcategory=SWEARING, **clauses)
    instance, values = _user(enforced, origin, own, override, mode, drift)
    settings = preferences_view(instance, values).settings()
    policy = enforced_policy(instance, values)
    finding = _filter(selected=saved, **saved_fields)
    before = finding.model_copy(deep=True)

    applied = apply_filter_defaults([finding], settings).filters[0]
    viewed = enforce_filter_policy([finding], policy).filters[0]
    other_policy = enforced_policy(instance, _other_personal_defaults(values))

    deny_matches = DENY in (enforced_disposition, own_disposition)
    assert applied.selected is (deny_matches if after_apply else saved)
    assert enforce_filter_policy([applied], policy).filters[0] == applied
    assert viewed.selected is (saved or (in_view and enforced_disposition is DENY))
    assert viewed.severity is (swearing if severity_enforced else finding.severity)
    assert viewed.mcf_channel is finding.mcf_channel
    assert other_policy == policy
    assert enforce_filter_policy([finding], other_policy).filters[0] == viewed
    assert finding == before
