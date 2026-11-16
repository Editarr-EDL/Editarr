"""What one user stores for filter defaults, read once from the database."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from scanner.models import FilterSelectionRule, McfCategory, Severity

FilterMode = Literal["live", "copy"]


@dataclass(frozen=True)
class RuleOverride:
    """A live-mode change to one inherited rule; `rule` holds in-place edits and keeps its ID."""

    rule_id: str
    disabled: bool
    rule: FilterSelectionRule | None = None


@dataclass(frozen=True)
class CopyValues:
    """An independent copy of the defaults; instance edits never reach it."""

    rules: tuple[FilterSelectionRule, ...]
    severities: Mapping[McfCategory, Severity]


@dataclass(frozen=True)
class Enforcement:
    """Instance rule IDs and subcategory severities the owner enforces on one user."""

    rule_ids: frozenset[str] = frozenset()
    subcategories: frozenset[McfCategory] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.rule_ids or self.subcategories)


@dataclass(frozen=True)
class PersonalFilterValues:
    """One user's stored mode, revisions, live overrides, personal rules, and copy.

    `copy` is None until the user first enters copy mode. Values for the mode the
    user is not in stay stored and unused.
    """

    user_id: int
    mode: FilterMode
    revision: int = 0
    policy_revision: int = 0
    overrides: Mapping[str, RuleOverride] = field(default_factory=dict)
    personal_rules: tuple[FilterSelectionRule, ...] = ()
    severity_overrides: Mapping[McfCategory, Severity] = field(default_factory=dict)
    copy: CopyValues | None = None
    enforcement: Enforcement = Enforcement()
