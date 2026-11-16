"""Owner-enforced filter policy and the policy revisions that track it (MU-14).

A user's `policy_revision` advances exactly when their enforced view changes: the
enforced set changes, or the owner edits or deletes an enforced instance rule or the
instance severity of an enforced subcategory. Each advance is one `FilterPolicyChange`.
Writers here stage changes in the caller's transaction and never commit.
"""

from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from scanner.db import (
    ExportScope,
    FilterEnforcementKind,
    MediaItemExportRow,
    UserFilterEnforcementRow,
    UserFilterPersonalRuleRow,
    UserFilterPreferenceRow,
    UserFilterRuleOverrideRow,
)
from scanner.models import FilterDefaultsSettings
from scanner.settings.filter_policy.effective import enforced_policy
from scanner.settings.filter_policy.events import FilterPolicyChange, PolicyChangeCause
from scanner.settings.filter_policy.updates import FilterPolicyUpdate
from scanner.settings.filter_policy.values import Enforcement

from ..write_lock import write_lock
from .filter_preference_rows import load_values
from .filter_preferences import (
    FilterPreferenceConflict,
    claim_revision,
    stale,
)
from .repository_settings import get_structured_filter_defaults_settings


def set_enforcement(
    db: Session, user_id: int, change: FilterPolicyUpdate, *, now: datetime
) -> FilterPolicyChange | None:
    """Replace the rules and severities enforced on `user_id`; None when nothing changed.

    Refuses a stale `policy_revision` and a rule the instance does not have when the
    write lock is taken. Stored personal values beneath newly enforced targets are
    kept, not rewritten. Holds the write lock for the caller to commit.
    """
    with write_lock(db):
        instance = get_structured_filter_defaults_settings(db)
        if not set(change.rules) <= {rule.id for rule in instance.selection_rules}:
            raise FilterPreferenceConflict(
                "unknown_filter_rule", "Only current instance rules can be enforced"
            )
        values = load_values(db, user_id)
        if change.policy_revision != values.policy_revision:
            raise stale()
        wanted = Enforcement(frozenset(change.rules), frozenset(change.severities))
        if wanted == values.enforcement:
            return None
        db.execute(
            update(UserFilterPreferenceRow)
            .where(UserFilterPreferenceRow.user_id == user_id)
            .values(policy_revision=change.policy_revision + 1, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        db.execute(
            delete(UserFilterEnforcementRow).where(UserFilterEnforcementRow.user_id == user_id)
        )
        db.flush()
        db.add_all(
            UserFilterEnforcementRow(
                user_id=user_id, kind=FilterEnforcementKind.SELECTION_RULE, target=rule_id
            )
            for rule_id in sorted(wanted.rule_ids)
        )
        db.add_all(
            UserFilterEnforcementRow(
                user_id=user_id, kind=FilterEnforcementKind.SEVERITY, target=subcategory.value
            )
            for subcategory in sorted(wanted.subcategories)
        )
        from .export_regeneration import queue_regenerations

        queue_regenerations(db)
    return FilterPolicyChange(user_id, change.policy_revision + 1, PolicyChangeCause.ENFORCEMENT)


def stage_instance_change(
    db: Session, before: FilterDefaultsSettings, after: FilterDefaultsSettings, now: datetime
) -> list[FilterPolicyChange]:
    """Prepare per-user state for replacing the instance defaults `before` with `after`.

    Refuses a new instance rule whose ID a personal rule already uses. Overrides of
    deleted rules go away, and so do overrides left from older data under a newly
    added ID. Enforcement of deleted rules goes away, and every user whose enforced
    view changes gets a policy change.
    """
    before_ids = {rule.id for rule in before.selection_rules}
    after_ids = {rule.id for rule in after.selection_rules}
    added, removed = after_ids - before_ids, before_ids - after_ids
    if added and db.scalar(select(func.count()).where(UserFilterPersonalRuleRow.id.in_(added))):
        raise FilterPreferenceConflict(
            "filter_rule_id_conflict", "A personal rule already uses that rule ID"
        )
    if added or removed:
        dropped = UserFilterRuleOverrideRow.rule_id.in_(added | removed)
        for user_id in db.scalars(
            select(UserFilterRuleOverrideRow.user_id).where(dropped).distinct()
        ).all():
            claim_revision(db, user_id, load_values(db, user_id).revision, now)
        db.execute(delete(UserFilterRuleOverrideRow).where(dropped))
    changes = []
    enforced_users = db.scalars(select(UserFilterEnforcementRow.user_id).distinct()).all()
    for user_id in sorted(enforced_users):
        values = load_values(db, user_id)
        if enforced_policy(before, values) == enforced_policy(after, values):
            continue
        db.execute(
            update(UserFilterPreferenceRow)
            .where(UserFilterPreferenceRow.user_id == user_id)
            .values(policy_revision=values.policy_revision + 1, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        changes.append(
            FilterPolicyChange(
                user_id, values.policy_revision + 1, PolicyChangeCause.INSTANCE_DEFAULTS
            )
        )
    if removed:
        db.execute(
            delete(UserFilterEnforcementRow).where(
                UserFilterEnforcementRow.kind == FilterEnforcementKind.SELECTION_RULE,
                UserFilterEnforcementRow.target.in_(removed),
            )
        )
    from .export_regeneration import queue_regenerations

    queue_regenerations(db)
    return changes


def outdated_policy_exports(db: Session, policy_owner_id: int) -> list[MediaItemExportRow]:
    """Default and personal exports made under an older policy of `policy_owner_id`.

    Selected by the review's policy owner only, never by the export's target user
    or publishing actor. Legacy records carry no enforced policy.
    """
    current = db.scalar(
        select(UserFilterPreferenceRow.policy_revision).where(
            UserFilterPreferenceRow.user_id == policy_owner_id
        )
    )
    return db.scalars(
        select(MediaItemExportRow)
        .where(
            MediaItemExportRow.scope != ExportScope.LEGACY,
            MediaItemExportRow.policy_owner_id == policy_owner_id,
            MediaItemExportRow.policy_revision < (current or 0),
        )
        .order_by(MediaItemExportRow.id)
    ).all()
