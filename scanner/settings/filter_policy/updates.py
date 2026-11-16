"""Request bodies for personal filter values, modes, and enforced policy."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from scanner.modeling.model_core import ReviewFilterId, ReviewRevision, ReviewWriteModel
from scanner.models import (
    FilterDefaultsUpdate,
    FilterSelectionRule,
    McfCategory,
    McfSeverityDefault,
)
from scanner.security.access.api_shapes import Reauthentication


def _unique(values, message: str) -> None:
    values = list(values)
    if len(values) != len(set(values)):
        raise ValueError(message)


class LiveRuleOverride(ReviewWriteModel):
    """Switch off and/or edit one inherited rule; `rule` keeps the inherited rule's ID."""

    rule_id: ReviewFilterId
    disabled: bool = False
    rule: FilterSelectionRule | None = None

    @model_validator(mode="after")
    def validate_change(self):
        if not self.disabled and self.rule is None:
            raise ValueError("An override must switch the rule off or edit it")
        if self.rule is not None and self.rule.id != self.rule_id:
            raise ValueError("An edited rule keeps the inherited rule ID")
        return self


class LivePreferencesUpdate(ReviewWriteModel):
    """The complete live-mode values. Omit enforced rules and severities."""

    mode: Literal["live"]
    revision: ReviewRevision
    overrides: list[LiveRuleOverride] = []
    personal_rules: list[FilterSelectionRule] = []
    severity_overrides: list[McfSeverityDefault] = []

    @model_validator(mode="after")
    def validate_unique(self):
        _unique((item.rule_id for item in self.overrides), "Overrides must name distinct rules")
        _unique((rule.id for rule in self.personal_rules), "Selection rule IDs must be unique")
        _unique(
            (item.subcategory for item in self.severity_overrides),
            "Severity override subcategories must be unique",
        )
        return self


class CopyPreferencesUpdate(ReviewWriteModel):
    """The complete copy: every rule, and a severity for every subcategory not enforced."""

    mode: Literal["copy"]
    revision: ReviewRevision
    rules: list[FilterSelectionRule]
    severities: list[McfSeverityDefault]

    @model_validator(mode="after")
    def validate_unique(self):
        _unique((rule.id for rule in self.rules), "Selection rule IDs must be unique")
        _unique(
            (item.subcategory for item in self.severities),
            "Severity subcategories must be unique",
        )
        return self


PreferencesUpdate = Annotated[
    LivePreferencesUpdate | CopyPreferencesUpdate, Field(discriminator="mode")
]


class FilterModeUpdate(ReviewWriteModel):
    """Set a user's mode."""

    revision: ReviewRevision
    mode: Literal["live", "copy"]


class DefaultModeUpdate(ReviewWriteModel):
    mode: Literal["live", "copy"]


class DefaultMode(ReviewWriteModel):
    mode: Literal["live", "copy"]


class InstanceDefaultsUpdate(FilterDefaultsUpdate):
    """Replacement instance defaults; the password is needed when they change enforced policy."""

    reauthentication: Reauthentication | None = None


class FilterPolicyUpdate(ReviewWriteModel):
    """The complete set of instance rule IDs and subcategories enforced on one user."""

    policy_revision: ReviewRevision
    rules: list[ReviewFilterId] = []
    severities: list[McfCategory] = []
    reauthentication: Reauthentication | None = None

    @model_validator(mode="after")
    def validate_unique(self):
        _unique(self.rules, "Enforced rule IDs must be unique")
        _unique(self.severities, "Enforced subcategories must be unique")
        return self
