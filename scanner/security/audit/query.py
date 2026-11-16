"""Permission-scoped, secret-free audit projections and page contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import AuditEventRow
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import CredentialKind, UserId
from scanner.security.access.records import (
    AuditEvent,
    AuditOutcome,
    MediaDecision,
    MediaDenial,
    MediaDenied,
)
from scanner.security.access.targets import NO_MEDIA, AccountRef, ResourceRef
from scanner.security.access.vocabulary import Role

from .actions import (
    ACTION_SPECS,
    ActorKind,
    AuditAction,
    AuditCategory,
    AuditEventRejected,
)
from .checks import check_event
from .store import retention_cutoff
from .visibility import action_visible


class AuditFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=1000000)
    page_size: int = Field(default=25, ge=1, le=100)
    category: AuditCategory | None = None
    action: AuditAction | None = None
    outcome: AuditOutcome | None = None
    actor_id: int | None = Field(default=None, gt=0)
    target_user_id: int | None = Field(default=None, gt=0)
    media_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    since: datetime | None = None
    until: datetime | None = None

    @model_validator(mode="after")
    def time_range(self):
        for value in (self.since, self.until):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Audit filter times must include a timezone")
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ValueError("Audit start must not follow its end")
        return self


class AuditEntry(BaseModel):
    event_id: str
    occurred_at: datetime
    category: AuditCategory
    action: AuditAction
    outcome: AuditOutcome
    actor_kind: ActorKind
    actor_id: int | None
    credential: CredentialKind | None
    target_user_id: int | None
    media_key: str | None
    resource: str | None
    details: dict[str, str | int | bool | None]


class AuditPage(BaseModel):
    events: list[AuditEntry]
    total: int
    pages: int
    page: int
    page_size: int
    retained_since: datetime
    retention_days: int = 90


def require_history(access: PrincipalAccess) -> None:
    """Audit is an administrator view, narrowed further by its live capability."""
    if access.principal.account.role is Role.USER:
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    require(access, Capability.AUDIT_READ, ResourceRef(None, NO_MEDIA, existence_visible=True))


def candidates(db: Session, now: datetime, event_id: str | None = None) -> list[AuditEventRow]:
    statement = select(AuditEventRow).where(AuditEventRow.occurred_at > retention_cutoff(now))
    if event_id is not None:
        statement = statement.where(AuditEventRow.event_id == event_id)
    return list(
        db.scalars(
            statement.order_by(
                AuditEventRow.occurred_at.desc(), AuditEventRow.id.desc()
            ).execution_options(populate_existing=True)
        )
    )


def _entry(row: AuditEventRow) -> AuditEntry | None:
    try:
        details = json.loads(row.details_json)
        if not isinstance(details, dict):
            return None
        at = row.occurred_at.replace(tzinfo=timezone.utc)
        event = AuditEvent(
            at,
            row.actor_id,
            CredentialKind(row.credential) if row.credential else None,
            row.action,
            AuditOutcome(row.outcome),
            target_user=row.target_user_id,
            media_key=row.media_key,
            resource=row.resource,
            details=details,
        )
        actor = ActorKind(row.actor_kind)
        action = check_event(event, actor)
        return AuditEntry(
            event_id=row.event_id,
            occurred_at=at,
            category=ACTION_SPECS[action].category,
            action=action,
            outcome=event.outcome,
            actor_kind=actor,
            actor_id=event.actor,
            credential=event.credential,
            target_user_id=event.target_user,
            media_key=event.media_key,
            resource=event.resource,
            details=dict(event.details),
        )
    except (ValueError, TypeError, AuditEventRejected):
        return None


def visible_entries(
    rows: list[AuditEventRow],
    access: PrincipalAccess,
    *,
    accounts: Mapping[UserId, AccountRef],
    media: Mapping[str, MediaDecision],
) -> list[AuditEntry]:
    """Scope targets before projecting rows, including direct-ID lookups."""
    require_history(access)
    visible = []
    for row in rows:
        owner = accounts.get(row.target_user_id) if row.target_user_id is not None else None
        if row.target_user_id is not None and owner is None:
            continue
        decision = (
            media.get(row.media_key, MediaDenied(MediaDenial.UNRESOLVED))
            if row.media_key
            else NO_MEDIA
        )
        if not isinstance(
            decide(access, Capability.AUDIT_READ, ResourceRef(owner, decision)), Allowed
        ):
            continue
        entry = _entry(row)
        if entry is not None and action_visible(
            entry.action, entry.target_user_id, entry.details, access
        ):
            visible.append(entry)
    return visible


def _matches(entry: AuditEntry, filters: AuditFilters) -> bool:
    return (
        all(
            getattr(filters, name) is None or getattr(entry, name) == getattr(filters, name)
            for name in ("category", "action", "outcome", "actor_id", "target_user_id", "media_key")
        )
        and (filters.since is None or entry.occurred_at >= filters.since)
        and (filters.until is None or entry.occurred_at <= filters.until)
    )


def scoped_page(
    db: Session,
    access: PrincipalAccess,
    filters: AuditFilters,
    *,
    accounts: Mapping[UserId, AccountRef],
    media: Mapping[str, MediaDecision],
    now: datetime,
) -> AuditPage:
    """Count and page only validated entries in the caller's current scope."""
    require_history(access)
    entries = visible_entries(candidates(db, now), access, accounts=accounts, media=media)
    entries = [entry for entry in entries if _matches(entry, filters)]
    start = (filters.page - 1) * filters.page_size
    return AuditPage(
        events=entries[start : start + filters.page_size],
        total=len(entries),
        pages=(len(entries) + filters.page_size - 1) // filters.page_size,
        page=filters.page,
        page_size=filters.page_size,
        retained_since=retention_cutoff(now).replace(tzinfo=timezone.utc),
    )
