"""Repository operations for media integrations and filter defaults."""

import json
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from scanner.db import (
    FilterSelectionRuleRow,
    FilterSettingsRow,
    JellyfinSettingsRow,
)
from scanner.models import (
    MCF_CATEGORY_TO_FAMILY,
    FilterDefaultsSettings,
    FilterDefaultsUpdate,
    FilterSelectionRule,
    JellyfinSettings,
    JellyfinSettingsUpdate,
    McfSeverityDefault,
)
from scanner.settings.filter_policy.events import FilterPolicyChange


class FilterSettingsRevisionConflict(ValueError):
    """The caller attempted to replace filter settings from an old revision."""


def get_jellyfin_settings(db: Session) -> JellyfinSettings:
    row = db.get(JellyfinSettingsRow, 1)
    if not row:
        return JellyfinSettings()
    return JellyfinSettings(
        server_url=row.server_url,
        api_key_configured=bool(row.api_key),
        last_checked_at=row.last_checked_at,
        last_error=row.last_error,
    )


def get_jellyfin_credentials(db: Session) -> tuple[str, str] | None:
    """Return the persisted runtime credentials; there is no environment fallback.

    Settings updates are the only supported way to change Jellyfin connection
    values. This prevents credentials submitted by a client or inherited from
    a deployment environment from silently taking precedence over the settings
    record a Editarr administrator sees in the UI.
    """
    row = db.get(JellyfinSettingsRow, 1)
    if not row or not row.server_url or not row.api_key:
        return None
    return row.server_url, row.api_key


def update_jellyfin_settings(
    db: Session, update: JellyfinSettingsUpdate, *, commit: bool = True
) -> JellyfinSettings:
    """Apply the supplied Jellyfin connection settings and return the safe view.

    With `commit=False` the change is only flushed, for a caller's own transaction.
    """
    row = db.get(JellyfinSettingsRow, 1)
    if row is None:
        row = JellyfinSettingsRow(id=1)
        db.add(row)
    if update.server_url is not None:
        row.server_url = update.server_url.strip() or None
    if update.api_key is not None:
        row.api_key = update.api_key.strip() or None
    if update.clear_api_key:
        row.api_key = None
    row.updated_at = datetime.now(timezone.utc)
    if not commit:
        db.flush()
        return get_jellyfin_settings(db)
    db.commit()
    db.refresh(row)
    return get_jellyfin_settings(db)


def record_jellyfin_connection(db: Session, error: str | None) -> None:
    row = db.get(JellyfinSettingsRow, 1)
    if not row:
        return
    row.last_checked_at = datetime.now(timezone.utc)
    row.last_error = error
    db.commit()


def get_structured_filter_defaults_settings(db: Session) -> FilterDefaultsSettings:
    """Decode persisted filter rules and severity defaults for the UI."""
    row = db.get(FilterSettingsRow, 1)
    if row is None:
        raise ValueError("Filter settings are not initialized")
    return FilterDefaultsSettings(
        revision=row.revision,
        selection_rules=[
            FilterSelectionRule(
                id=rule.id,
                disposition=rule.disposition,
                evidence_source=rule.evidence_source,
                category=rule.category,
                subcategory=rule.subcategory,
                label_contains=rule.label_contains,
                severity=rule.severity,
                mcf_channel=rule.mcf_channel,
            )
            for rule in row.selection_rules
        ],
        severity_defaults=[
            McfSeverityDefault(subcategory=default.subcategory, severity=default.severity)
            for default in row.severity_defaults
        ],
    )


def update_filter_defaults_settings(
    db: Session, settings_update: FilterDefaultsUpdate
) -> FilterDefaultsSettings:
    """Validate and persist filter defaults using the supplied revision."""
    return replace_filter_defaults(db, settings_update)[0]


def replace_filter_defaults(
    db: Session,
    settings_update: FilterDefaultsUpdate,
    *,
    stage_changes: Callable[[list[FilterPolicyChange]], None] | None = None,
):
    """Persist instance defaults and return them with the policy changes they caused.

    Per-user state follows in the same transaction (see `filter_enforcement`).
    `stage_changes` receives the policy changes before the commit, so their audit
    events commit or roll back with the edit.
    """
    from .filter_enforcement import stage_instance_change

    row = db.get(FilterSettingsRow, 1)
    if row is None:
        raise ValueError("Filter settings are not initialized")
    persisted_revision = db.scalar(
        select(FilterSettingsRow.revision).where(FilterSettingsRow.id == 1)
    )
    if persisted_revision != settings_update.revision:
        raise FilterSettingsRevisionConflict("Filter settings revision is stale")
    db.expire(row, ["selection_rules", "severity_defaults"])
    current = get_structured_filter_defaults_settings(db)

    severity_by_subcategory = {
        item.subcategory: item.severity for item in settings_update.severity_defaults
    }
    normalized = FilterDefaultsSettings(
        revision=current.revision,
        selection_rules=settings_update.selection_rules,
        severity_defaults=[
            McfSeverityDefault(
                subcategory=subcategory,
                severity=severity_by_subcategory[subcategory],
            )
            for subcategory in MCF_CATEGORY_TO_FAMILY
        ],
    )
    if normalized == current:
        unchanged = db.execute(
            update(FilterSettingsRow)
            .where(
                FilterSettingsRow.id == 1,
                FilterSettingsRow.revision == settings_update.revision,
            )
            .values(revision=settings_update.revision)
            .execution_options(synchronize_session=False)
        )
        if unchanged.rowcount != 1:
            db.rollback()
            raise FilterSettingsRevisionConflict("Filter settings revision is stale")
        db.rollback()
        return current, []

    now = datetime.now(timezone.utc)
    claimed = db.execute(
        update(FilterSettingsRow)
        .where(
            FilterSettingsRow.id == 1,
            FilterSettingsRow.revision == settings_update.revision,
        )
        .values(revision=settings_update.revision + 1, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise FilterSettingsRevisionConflict("Filter settings revision is stale")
    try:
        changes = stage_instance_change(db, current, normalized, now)
    except ValueError:
        db.rollback()
        raise

    row.selection_rules.clear()
    db.flush()
    row.selection_rules.extend(
        FilterSelectionRuleRow(
            id=rule.id,
            position=position,
            disposition=rule.disposition,
            evidence_source=rule.evidence_source,
            category=rule.category,
            subcategory=rule.subcategory,
            label_contains=rule.label_contains,
            severity=rule.severity,
            mcf_channel=rule.mcf_channel,
        )
        for position, rule in enumerate(normalized.selection_rules)
    )
    defaults_by_subcategory = {default.subcategory: default for default in row.severity_defaults}
    for position, default in enumerate(normalized.severity_defaults):
        persisted = defaults_by_subcategory[default.subcategory.value]
        persisted.position = position
        persisted.severity = default.severity
    if stage_changes is not None and changes:
        stage_changes(changes)
    db.commit()
    return get_structured_filter_defaults_settings(db), changes


def _decode_filter_values(raw_value: str | None) -> list[str]:
    """Decode a stored JSON list of filter tokens, treating absent data as empty."""
    if not raw_value:
        return []
    try:
        values = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized:
            cleaned.append(normalized)
    return cleaned


def _encode_filter_values(values: list[str]) -> str:
    cleaned = [value.strip() for value in values if value and value.strip()]
    return json.dumps(cleaned)
