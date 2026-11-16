"""Derive one user's effective filter defaults and enforced policy without changing inputs.

Enforced rules and severities always take their current instance values, in every
mode, so a mode change cannot escape enforcement. In live mode the user's other
values sit on top of the instance defaults; in copy mode they replace them. Deny
wins over Allow regardless of position, so rule order is for display only:
inherited rules in instance order, then the user's own rules in the user's order.
"""

from typing import Literal

from pydantic import BaseModel

from scanner.models import (
    FilterDefaultsSettings,
    FilterSelectionRule,
    McfCategory,
    McfSeverityDefault,
    Severity,
)
from scanner.settings.filter_defaults import EnforcedFilterPolicy

from .values import CopyValues, FilterMode, PersonalFilterValues


class EffectiveRule(BaseModel):
    """One rule as it applies to the user. A disabled rule is shown but never applies."""

    rule: FilterSelectionRule
    origin: Literal["instance", "personal", "copy"]
    edited: bool = False
    disabled: bool = False
    enforced: bool = False


class EffectiveSeverity(BaseModel):
    subcategory: McfCategory
    severity: Severity
    origin: Literal["instance", "override", "copy"]
    enforced: bool = False


class FilterPreferencesView(BaseModel):
    """A user's effective filter defaults with the provenance of each value.

    `revision` guards edits to the user's values and `policy_revision` names the
    enforced policy. `mode_locked` is true while any policy is enforced, so the
    user may not choose their own mode.
    """

    user_id: int
    mode: FilterMode
    mode_locked: bool
    revision: int
    policy_revision: int
    instance_revision: int
    rules: list[EffectiveRule]
    severities: list[EffectiveSeverity]
    enforced_rules: list[str]
    enforced_severities: list[McfCategory]

    def settings(self) -> FilterDefaultsSettings:
        """The rules and severities that fill and Apply use for this user."""
        return FilterDefaultsSettings(
            revision=self.revision,
            selection_rules=[entry.rule for entry in self.rules if not entry.disabled],
            severity_defaults=[
                McfSeverityDefault(subcategory=entry.subcategory, severity=entry.severity)
                for entry in self.severities
            ],
        )


def enforced_policy(
    instance: FilterDefaultsSettings, values: PersonalFilterValues
) -> EnforcedFilterPolicy:
    """The enforced rules and severities at their current instance values."""
    enforcement = values.enforcement
    return EnforcedFilterPolicy(
        policy_revision=values.policy_revision,
        rules=tuple(rule for rule in instance.selection_rules if rule.id in enforcement.rule_ids),
        severities={
            item.subcategory: item.severity
            for item in instance.severity_defaults
            if item.subcategory in enforcement.subcategories
        },
    )


def live_copy(instance: FilterDefaultsSettings, values: PersonalFilterValues) -> CopyValues:
    """What the user sees in live mode, ignoring enforcement: the values a new copy starts from."""
    rules = []
    for rule in instance.selection_rules:
        override = values.overrides.get(rule.id)
        if override is None:
            rules.append(rule)
        elif not override.disabled:
            rules.append(override.rule or rule)
    return CopyValues(
        rules=(*rules, *_personal_rules(instance, values)),
        severities={
            item.subcategory: values.severity_overrides.get(item.subcategory, item.severity)
            for item in instance.severity_defaults
        },
    )


def preferences_view(
    instance: FilterDefaultsSettings, values: PersonalFilterValues
) -> FilterPreferencesView:
    """Combine the instance defaults, the user's values, and enforcement into one view."""
    mode = values.mode
    enforcement = values.enforcement
    if mode == "live":
        rules = _live_rules(instance, values)
    else:
        rules = _copy_rules(instance, values)
    return FilterPreferencesView(
        user_id=values.user_id,
        mode=mode,
        mode_locked=bool(enforcement),
        revision=values.revision,
        policy_revision=values.policy_revision,
        instance_revision=instance.revision,
        rules=rules,
        severities=_severities(instance, values, mode),
        enforced_rules=[
            rule.id for rule in instance.selection_rules if rule.id in enforcement.rule_ids
        ],
        enforced_severities=[
            item.subcategory
            for item in instance.severity_defaults
            if item.subcategory in enforcement.subcategories
        ],
    )


def _live_rules(
    instance: FilterDefaultsSettings, values: PersonalFilterValues
) -> list[EffectiveRule]:
    rules = []
    for rule in instance.selection_rules:
        override = values.overrides.get(rule.id)
        if rule.id in values.enforcement.rule_ids or override is None:
            enforced = rule.id in values.enforcement.rule_ids
            rules.append(EffectiveRule(rule=rule, origin="instance", enforced=enforced))
            continue
        rules.append(
            EffectiveRule(
                rule=override.rule or rule,
                origin="instance",
                edited=override.rule is not None,
                disabled=override.disabled,
            )
        )
    rules.extend(
        EffectiveRule(rule=rule, origin="personal") for rule in _personal_rules(instance, values)
    )
    return rules


def _personal_rules(
    instance: FilterDefaultsSettings, values: PersonalFilterValues
) -> tuple[FilterSelectionRule, ...]:
    """The user's rules, without any that older data stored under an instance rule's ID."""
    instance_ids = {rule.id for rule in instance.selection_rules}
    return tuple(rule for rule in values.personal_rules if rule.id not in instance_ids)


def _copy_rules(
    instance: FilterDefaultsSettings, values: PersonalFilterValues
) -> list[EffectiveRule]:
    enforced_ids = values.enforcement.rule_ids
    copy = values.copy or CopyValues(rules=tuple(instance.selection_rules), severities={})
    return [
        *(
            EffectiveRule(rule=rule, origin="instance", enforced=True)
            for rule in instance.selection_rules
            if rule.id in enforced_ids
        ),
        *(
            EffectiveRule(rule=rule, origin="copy")
            for rule in copy.rules
            if rule.id not in enforced_ids
        ),
    ]


def _severities(
    instance: FilterDefaultsSettings, values: PersonalFilterValues, mode: FilterMode
) -> list[EffectiveSeverity]:
    copied = values.copy.severities if values.copy is not None else {}
    severities = []
    for item in instance.severity_defaults:
        subcategory = item.subcategory
        if subcategory in values.enforcement.subcategories:
            entry = EffectiveSeverity(
                subcategory=subcategory, severity=item.severity, origin="instance", enforced=True
            )
        elif mode == "copy":
            severity = copied.get(subcategory, item.severity)
            entry = EffectiveSeverity(subcategory=subcategory, severity=severity, origin="copy")
        elif subcategory in values.severity_overrides:
            severity = values.severity_overrides[subcategory]
            entry = EffectiveSeverity(subcategory=subcategory, severity=severity, origin="override")
        else:
            entry = EffectiveSeverity(
                subcategory=subcategory, severity=item.severity, origin="instance"
            )
        severities.append(entry)
    return severities
