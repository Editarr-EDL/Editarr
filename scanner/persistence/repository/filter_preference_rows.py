"""Read one user's stored filter values from their rows (MU-14)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import (
    UserFilterCopyRuleRow,
    UserFilterCopySeverityRow,
    UserFilterEnforcementRow,
    UserFilterPersonalRuleRow,
    UserFilterPreferenceRow,
    UserFilterRuleOverrideRow,
    UserFilterSeverityOverrideRow,
)
from scanner.models import FilterSelectionRule, McfCategory
from scanner.settings.filter_policy.values import (
    CopyValues,
    Enforcement,
    PersonalFilterValues,
    RuleOverride,
)

_RULE_FIELDS = (
    "disposition",
    "evidence_source",
    "category",
    "subcategory",
    "label_contains",
    "severity",
    "mcf_channel",
)


def rule_from_row(row, rule_id: str | None = None) -> FilterSelectionRule:
    fields = {name: getattr(row, name) for name in _RULE_FIELDS}
    return FilterSelectionRule.model_validate({"id": rule_id or row.id, **fields})


def rule_columns(rule: FilterSelectionRule) -> dict:
    return {name: getattr(rule, name) for name in _RULE_FIELDS}


def load_values(db: Session, user_id: int) -> PersonalFilterValues:
    """Read everything one user stores, in display order."""
    row = db.get_one(UserFilterPreferenceRow, user_id)

    def rows(table, *order):
        return db.scalars(select(table).where(table.user_id == user_id).order_by(*order)).all()

    overrides = {
        item.rule_id: RuleOverride(
            item.rule_id,
            bool(item.disabled),
            rule_from_row(item, item.rule_id) if item.disposition is not None else None,
        )
        for item in rows(UserFilterRuleOverrideRow, UserFilterRuleOverrideRow.rule_id)
    }
    copy_severities = rows(UserFilterCopySeverityRow, UserFilterCopySeverityRow.position)
    copy = None
    if copy_severities:
        copy = CopyValues(
            rules=tuple(
                rule_from_row(item)
                for item in rows(UserFilterCopyRuleRow, UserFilterCopyRuleRow.position)
            ),
            severities={McfCategory(item.subcategory): item.severity for item in copy_severities},
        )
    enforcements = rows(UserFilterEnforcementRow, UserFilterEnforcementRow.target)
    return PersonalFilterValues(
        user_id=user_id,
        mode=row.mode.value,
        revision=row.revision,
        policy_revision=row.policy_revision,
        overrides=overrides,
        personal_rules=tuple(
            rule_from_row(item)
            for item in rows(UserFilterPersonalRuleRow, UserFilterPersonalRuleRow.position)
        ),
        severity_overrides={
            McfCategory(item.subcategory): item.severity
            for item in rows(
                UserFilterSeverityOverrideRow, UserFilterSeverityOverrideRow.subcategory
            )
        },
        copy=copy,
        enforcement=Enforcement(
            rule_ids=frozenset(e.target for e in enforcements if e.kind == "selection_rule"),
            subcategories=frozenset(
                McfCategory(e.target) for e in enforcements if e.kind == "severity"
            ),
        ),
    )
