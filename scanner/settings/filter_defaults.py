"""Pure structured filter-default evaluation."""

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from scanner.modeling.model_core import channel_for_source
from scanner.models import (
    FilterDefaultsApplyResult,
    FilterDefaultsSettings,
    FilterRuleDisposition,
    FilterSelectionRule,
    McfCategory,
    NewManualReviewFilter,
    ReviewFilterInput,
    Severity,
)

DefaultableFilter = TypeVar("DefaultableFilter", ReviewFilterInput, NewManualReviewFilter)


def evaluate_filter_defaults(
    filters: Sequence[ReviewFilterInput],
    settings: FilterDefaultsSettings,
    *,
    overwrite_existing: bool,
) -> FilterDefaultsApplyResult:
    """Return copied filters with deterministic defaults and selection rules applied."""
    evaluated, selection_changes, severity_changes, channel_changes = _evaluate_copies(
        filters, settings, overwrite_existing=overwrite_existing
    )
    return FilterDefaultsApplyResult(
        filters=evaluated,
        selection_changes=selection_changes,
        severity_changes=severity_changes,
        channel_changes=channel_changes,
    )


def _evaluate_copies(
    filters: Sequence[DefaultableFilter],
    settings: FilterDefaultsSettings,
    *,
    overwrite_existing: bool,
) -> tuple[list[DefaultableFilter], int, int, int]:
    """Evaluate ordered filter rules against the supplied finding copies."""
    severity_defaults = {item.subcategory: item.severity for item in settings.severity_defaults}
    evaluated: list[DefaultableFilter] = []
    selection_changes = 0
    severity_changes = 0
    channel_changes = 0

    for source_filter in filters:
        severity = source_filter.severity
        default_severity = (
            severity_defaults.get(source_filter.subcategory)
            if source_filter.subcategory is not None
            else None
        )
        if default_severity is not None and (overwrite_existing or severity is None):
            severity = default_severity

        channel = source_filter.mcf_channel
        default_channel = channel_for_source(source_filter.source)
        if overwrite_existing or channel is None:
            channel = default_channel

        candidate = source_filter.model_copy(update={"severity": severity, "mcf_channel": channel})
        selected = _evaluate_selection(candidate, settings.selection_rules)
        result = candidate.model_copy(update={"selected": selected})
        evaluated.append(result)

        selection_changes += selected != source_filter.selected
        severity_changes += severity != source_filter.severity
        channel_changes += channel != source_filter.mcf_channel

    return evaluated, selection_changes, severity_changes, channel_changes


def apply_filter_defaults(
    filters: Sequence[ReviewFilterInput], settings: FilterDefaultsSettings
) -> FilterDefaultsApplyResult:
    """Explicitly recompute severity, channel, and selection on draft filter copies.

    Feed saved content, never `enforce_filter_policy` output: the view may select a
    finding Apply would leave alone. `settings` include enforced rules and
    severities, so Deny wins with the floor included.
    """
    return evaluate_filter_defaults(filters, settings, overwrite_existing=True)


def fill_filter_defaults(
    filters: Sequence[ReviewFilterInput], settings: FilterDefaultsSettings
) -> FilterDefaultsApplyResult:
    """Fill missing severity/channel and evaluate selection on new filter copies."""
    return evaluate_filter_defaults(filters, settings, overwrite_existing=False)


def fill_manual_filter_defaults(
    filters: Sequence[NewManualReviewFilter], settings: FilterDefaultsSettings
) -> list[NewManualReviewFilter]:
    """Fill automatic defaults on copied manual filters before review persistence."""
    evaluated, _selection_changes, _severity_changes, _channel_changes = _evaluate_copies(
        filters, settings, overwrite_existing=False
    )
    return evaluated


@dataclass(frozen=True)
class EnforcedFilterPolicy:
    """The owner-enforced part of one user's filter policy, at `policy_revision`.

    `rules` are the enforced instance selection rules with their current instance
    fields; `severities` maps each enforced subcategory to its instance severity.
    """

    policy_revision: int
    rules: tuple[FilterSelectionRule, ...] = ()
    severities: Mapping[McfCategory, Severity] = field(default_factory=dict)


@dataclass(frozen=True)
class EnforcedFilters(Generic[DefaultableFilter]):
    """Effective copies of saved filters and how many differ from the saved values."""

    filters: tuple[DefaultableFilter, ...]
    selection_changes: int
    severity_changes: int


def enforce_filter_policy(
    filters: Sequence[DefaultableFilter], policy: EnforcedFilterPolicy
) -> EnforcedFilters[DefaultableFilter]:
    """Return the effective review view of saved filters under an enforced policy.

    The view reads saved content and the enforced policy only, never the owner's
    non-enforced defaults, so it changes only with saved content or
    `policy_revision`. Enforced severities replace the saved severity of their
    subcategory. Enforcement is a floor: a finding is selected when an enforced
    Deny matches it with that severity and its effective channel; every other finding
    keeps its saved selection. An enforced Allow never unselects, because the saved
    selection may come from the user's own Deny. Apply includes the enforced rules
    and severities, so the view of an Applied result is that result. Never feed
    this output to Apply; Apply reads saved content. Inputs are never changed.
    """
    floor = [rule for rule in policy.rules if rule.disposition is FilterRuleDisposition.DENY]
    effective = []
    selection_changes = severity_changes = 0
    for saved in filters:
        severity = policy.severities.get(saved.subcategory, saved.severity)
        candidate = saved.model_copy(update={"severity": severity})
        selected = saved.selected or any(_rule_matches(rule, candidate) for rule in floor)
        effective.append(candidate.model_copy(update={"selected": selected}))
        selection_changes += selected != saved.selected
        severity_changes += severity != saved.severity
    return EnforcedFilters(tuple(effective), selection_changes, severity_changes)


def _evaluate_selection(
    candidate: ReviewFilterInput | NewManualReviewFilter,
    rules: Sequence[FilterSelectionRule],
) -> bool:
    matching_dispositions = {rule.disposition for rule in rules if _rule_matches(rule, candidate)}
    if FilterRuleDisposition.DENY in matching_dispositions:
        return True
    if FilterRuleDisposition.ALLOW in matching_dispositions:
        return False
    return candidate.selected


def _rule_matches(
    rule: FilterSelectionRule,
    candidate: ReviewFilterInput | NewManualReviewFilter,
) -> bool:
    """Check whether a filter rule matches a finding and its source metadata."""
    return (
        (rule.evidence_source is None or rule.evidence_source == candidate.source)
        and (rule.category is None or rule.category == candidate.category)
        and (rule.subcategory is None or rule.subcategory == candidate.subcategory)
        and (rule.severity is None or rule.severity == candidate.severity)
        and (
            rule.mcf_channel is None
            or rule.mcf_channel == (candidate.mcf_channel or channel_for_source(candidate.source))
        )
        and (
            rule.label_contains is None
            or _contains_token_sequence(candidate.label, rule.label_contains)
        )
    )


def _contains_token_sequence(value: str | None, expected: str) -> bool:
    value_tokens = _normalized_tokens(value or "")
    expected_tokens = _normalized_tokens(expected)
    if not expected_tokens or len(expected_tokens) > len(value_tokens):
        return False
    width = len(expected_tokens)
    return any(
        value_tokens[position : position + width] == expected_tokens
        for position in range(len(value_tokens) - width + 1)
    )


def _normalized_tokens(value: str) -> list[str]:
    """Normalize text into comparable tokens for filter rule matching."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        if unicodedata.category(character)[0] in {"L", "N"}:
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens
