"""Per-user filter values, modes, and copies (MU-14).

Every account has a `user_filter_preferences` row, created with the account in the
instance's starting mode, that holds its mode and guards every value with
`revision`. Entering copy mode takes a new copy of what the user saw in live mode,
so a user in copy mode always has one. Values for enforced rules and severities
stay stored beneath the enforced view and are never written by these functions.

Each SELECT runs outside a transaction and sees the latest commit, so reads made of
several SELECTs check that no filter write committed in between. Writers take the
write lock before their first read instead (`write_lock`), so what they decide on
cannot change before they commit.
"""

from dataclasses import replace
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from scanner.db import (
    FilterPreferenceMode,
    FilterSettingsRow,
    UserFilterCopyRuleRow,
    UserFilterCopySeverityRow,
    UserFilterPersonalRuleRow,
    UserFilterPreferenceRow,
    UserFilterRuleOverrideRow,
    UserFilterSeverityOverrideRow,
)
from scanner.models import FilterDefaultsSettings, McfCategory
from scanner.settings.filter_defaults import EnforcedFilterPolicy
from scanner.settings.filter_policy.effective import (
    FilterPreferencesView,
    enforced_policy,
    live_copy,
    preferences_view,
)
from scanner.settings.filter_policy.updates import (
    CopyPreferencesUpdate,
    FilterModeUpdate,
    LivePreferencesUpdate,
)
from scanner.settings.filter_policy.values import (
    CopyValues,
    Enforcement,
    FilterMode,
    PersonalFilterValues,
)

from ..write_lock import write_lock
from .filter_preference_rows import load_values, rule_columns
from .repository_settings import get_structured_filter_defaults_settings

_SNAPSHOT_ATTEMPTS = 5


class FilterPreferenceConflict(ValueError):
    """A refused write, named by a stable `code` that the HTTP layer returns."""

    def __init__(self, code: str, detail: str, status: int = 409) -> None:
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def stale() -> FilterPreferenceConflict:
    return FilterPreferenceConflict("stale_settings_revision", "Filter settings revision is stale")


def default_mode(db: Session) -> FilterMode:
    mode = db.scalar(select(FilterSettingsRow.default_mode).where(FilterSettingsRow.id == 1))
    return FilterPreferenceMode(mode).value


def _snapshot(db: Session, user_id: int) -> tuple[FilterDefaultsSettings, PersonalFilterValues]:
    """The instance defaults and the user's values as of one commit.

    Every filter write advances the instance revision or the user's revision or
    policy revision. A read that reports the stamp it started and ended with
    therefore saw no commit in between. Raises `stale` when writes keep overtaking
    the read.
    """
    stamp = (
        select(
            FilterSettingsRow.revision,
            UserFilterPreferenceRow.revision,
            UserFilterPreferenceRow.policy_revision,
        )
        .join(UserFilterPreferenceRow, UserFilterPreferenceRow.user_id == user_id)
        .where(FilterSettingsRow.id == 1)
    )
    for _ in range(_SNAPSHOT_ATTEMPTS):
        before = db.execute(stamp).one()
        instance = get_structured_filter_defaults_settings(db)
        values = load_values(db, user_id)
        read = (instance.revision, values.revision, values.policy_revision)
        if read == tuple(before) and db.execute(stamp).one() == before:
            return instance, values
        # The identity map still holds what the overtaken read loaded.
        db.expire_all()
    raise stale()


def filter_preferences_view(db: Session, user_id: int) -> FilterPreferencesView:
    """The user's effective defaults with provenance, as Settings and consumers see them."""
    return preferences_view(*_snapshot(db, user_id))


def effective_filter_settings(db: Session, user_id: int) -> FilterDefaultsSettings:
    """The rules and severities that fill and Apply use for reviews `user_id` owns."""
    return filter_preferences_view(db, user_id).settings()


def base_filter_settings(db: Session, user_id: int | None) -> FilterDefaultsSettings:
    """Defaults beneath enforcement, for recoverable new saved copies."""
    if user_id is None:
        return get_structured_filter_defaults_settings(db)
    instance, values = _snapshot(db, user_id)
    return preferences_view(instance, replace(values, enforcement=Enforcement())).settings()


def enforced_filter_policy(db: Session, user_id: int) -> EnforcedFilterPolicy:
    """The policy that every review `user_id` owns is viewed, edited, and exported under."""
    instance, values = _snapshot(db, user_id)
    return enforced_policy(instance, values)


def start_preferences(db: Session, user_id: int, now: datetime) -> None:
    """Give a new account its row in the instance's starting mode, at revision 0.

    An account that starts in copy mode gets a copy of the current defaults. The
    caller holds the write lock and commits.
    """
    mode = default_mode(db)
    db.add(
        UserFilterPreferenceRow(
            user_id=user_id,
            mode=FilterPreferenceMode(mode),
            revision=0,
            policy_revision=0,
            updated_at=now,
        )
    )
    db.flush()
    if mode == "copy":
        instance = get_structured_filter_defaults_settings(db)
        take_copy(db, user_id, live_copy(instance, PersonalFilterValues(user_id, mode)))


def claim_revision(db: Session, user_id: int, expected: int, now: datetime, step: int = 1):
    """Advance the user's edit revision from `expected`, or roll back and raise `stale`."""
    row = db.get_one(UserFilterPreferenceRow, user_id)
    claimed = db.execute(
        update(UserFilterPreferenceRow)
        .where(
            UserFilterPreferenceRow.user_id == user_id,
            UserFilterPreferenceRow.revision == expected,
        )
        .values(revision=expected + step, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise stale()
    db.expire(row)
    return row


def take_copy(db: Session, user_id: int, copy: CopyValues) -> None:
    """Replace the user's copy. The caller holds the preference row and its revision."""
    db.execute(delete(UserFilterCopyRuleRow).where(UserFilterCopyRuleRow.user_id == user_id))
    db.execute(
        delete(UserFilterCopySeverityRow).where(UserFilterCopySeverityRow.user_id == user_id)
    )
    db.flush()
    db.add_all(
        UserFilterCopyRuleRow(user_id=user_id, id=rule.id, position=position, **rule_columns(rule))
        for position, rule in enumerate(copy.rules)
    )
    db.add_all(
        UserFilterCopySeverityRow(
            user_id=user_id, subcategory=subcategory.value, position=position, severity=severity
        )
        for position, (subcategory, severity) in enumerate(copy.severities.items())
    )


def save_preferences(
    db: Session,
    user_id: int,
    change: LivePreferencesUpdate | CopyPreferencesUpdate,
    *,
    now: datetime,
) -> FilterPreferencesView:
    """Replace the user's values for their current mode and commit.

    Refuses a stale revision, a body for the other mode, a value for an enforced
    rule or severity, an override of an unknown rule, and a personal rule that
    reuses an instance rule ID, all as of when the write lock was taken.
    """
    with write_lock(db):
        instance = get_structured_filter_defaults_settings(db)
        values = load_values(db, user_id)
        if change.mode != values.mode:
            raise FilterPreferenceConflict(
                "filter_mode_changed", "The filter mode changed; reload the preferences"
            )
        if isinstance(change, LivePreferencesUpdate):
            _check_live(instance, values, change)
        else:
            _check_copy(values, change)
        claim_revision(db, user_id, change.revision, now)
        if isinstance(change, LivePreferencesUpdate):
            _write_live(db, user_id, values, change)
        else:
            _write_copy(db, instance, values, change)
        db.commit()
    return filter_preferences_view(db, user_id)


def _enforced(message: str) -> FilterPreferenceConflict:
    return FilterPreferenceConflict("filter_policy_enforced", message)


def _check_live(
    instance: FilterDefaultsSettings, values: PersonalFilterValues, change: LivePreferencesUpdate
) -> None:
    instance_ids = {rule.id for rule in instance.selection_rules}
    enforcement = values.enforcement
    for override in change.overrides:
        if override.rule_id not in instance_ids:
            raise FilterPreferenceConflict(
                "unknown_filter_rule", "An override names a rule the instance no longer has"
            )
        if override.rule_id in enforcement.rule_ids:
            raise _enforced("An enforced rule cannot be switched off or edited")
    if instance_ids & {rule.id for rule in change.personal_rules}:
        raise FilterPreferenceConflict(
            "filter_rule_id_conflict", "A personal rule cannot reuse an instance rule ID"
        )
    if any(item.subcategory in enforcement.subcategories for item in change.severity_overrides):
        raise _enforced("An enforced severity cannot be overridden")


def _check_copy(values: PersonalFilterValues, change: CopyPreferencesUpdate) -> None:
    enforcement = values.enforcement
    if any(rule.id in enforcement.rule_ids for rule in change.rules):
        raise _enforced("An enforced rule cannot be replaced in a copy")
    given = {item.subcategory for item in change.severities}
    if given & enforcement.subcategories:
        raise _enforced("An enforced severity cannot be replaced in a copy")
    if given | enforcement.subcategories != set(McfCategory):
        raise FilterPreferenceConflict(
            "filter_severities_incomplete",
            "A copy needs a severity for every subcategory that is not enforced",
            422,
        )


def _write_live(
    db: Session, user_id: int, values: PersonalFilterValues, change: LivePreferencesUpdate
) -> None:
    enforcement = values.enforcement
    db.execute(
        delete(UserFilterRuleOverrideRow).where(
            UserFilterRuleOverrideRow.user_id == user_id,
            UserFilterRuleOverrideRow.rule_id.not_in(enforcement.rule_ids),
        )
    )
    db.execute(
        delete(UserFilterPersonalRuleRow).where(UserFilterPersonalRuleRow.user_id == user_id)
    )
    db.execute(
        delete(UserFilterSeverityOverrideRow).where(
            UserFilterSeverityOverrideRow.user_id == user_id,
            UserFilterSeverityOverrideRow.subcategory.not_in(
                [item.value for item in enforcement.subcategories]
            ),
        )
    )
    db.flush()
    for override in change.overrides:
        columns = rule_columns(override.rule) if override.rule is not None else {}
        db.add(
            UserFilterRuleOverrideRow(
                user_id=user_id, rule_id=override.rule_id, disabled=override.disabled, **columns
            )
        )
    db.add_all(
        UserFilterPersonalRuleRow(
            user_id=user_id, id=rule.id, position=position, **rule_columns(rule)
        )
        for position, rule in enumerate(change.personal_rules)
    )
    db.add_all(
        UserFilterSeverityOverrideRow(
            user_id=user_id, subcategory=item.subcategory.value, severity=item.severity
        )
        for item in change.severity_overrides
    )


def _write_copy(
    db: Session,
    instance: FilterDefaultsSettings,
    values: PersonalFilterValues,
    change: CopyPreferencesUpdate,
) -> None:
    """Write the copy, keeping stored values for enforced targets beneath the policy."""
    enforcement = values.enforcement
    stored = values.copy or live_copy(instance, values)
    kept = tuple(rule for rule in stored.rules if rule.id in enforcement.rule_ids)
    severities = {item.subcategory: item.severity for item in change.severities}
    instance_severities = {item.subcategory: item.severity for item in instance.severity_defaults}
    for subcategory in enforcement.subcategories:
        severities[subcategory] = stored.severities.get(
            subcategory, instance_severities[subcategory]
        )
    ordered = {subcategory: severities[subcategory] for subcategory in instance_severities}
    take_copy(db, values.user_id, CopyValues(rules=(*change.rules, *kept), severities=ordered))


def set_mode(
    db: Session, user_id: int, change: FilterModeUpdate, *, self_service: bool, now: datetime
) -> FilterPreferencesView:
    """Set the user's mode and commit; entering copy mode takes a new copy.

    A user already in copy mode keeps their copy. A user may not choose their own
    mode while any policy is enforced on them.
    """
    with write_lock(db):
        values = load_values(db, user_id)
        if self_service and values.enforcement:
            raise _enforced("Your filter mode is managed while a filter policy is enforced")
        row = claim_revision(db, user_id, change.revision, now)
        row.mode = FilterPreferenceMode(change.mode)
        if change.mode == "copy" and values.mode == "live":
            instance = get_structured_filter_defaults_settings(db)
            take_copy(db, user_id, live_copy(instance, values))
        db.commit()
    return filter_preferences_view(db, user_id)


def set_default_mode(db: Session, mode: FilterMode) -> FilterMode:
    """Change the mode new accounts start in and commit; existing accounts keep theirs."""
    with write_lock(db):
        db.execute(
            update(FilterSettingsRow)
            .where(FilterSettingsRow.id == 1)
            .values(default_mode=FilterPreferenceMode(mode))
            .execution_options(synchronize_session=False)
        )
        db.commit()
    return mode
