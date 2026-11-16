"""Write audit events and expire them after `AUDIT_RETENTION`.

`stage_event` adds an event to the caller's transaction, so a change and its
event commit together or not at all. `record_event` stores an event on its own
for refusals and failures, where nothing else is committed; a storage failure is
logged without payload and never alters the caller's refusal.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from scanner.db import AuditEventRow, MediaItemRow, UserRow
from scanner.security.access.records import AUDIT_RETENTION, AuditEvent

from .actions import ActorKind, AuditEventRejected
from .checks import check_event

_logger = logging.getLogger(__name__)


def stage_event(
    db: Session,
    event: AuditEvent,
    *,
    actor_kind: ActorKind = ActorKind.USER,
    request_id: str | None = None,
    event_id: str | None = None,
) -> str:
    """Validate one event and add it to the open transaction without committing.

    Raises `AuditEventRejected` for an unregistered shape or an actor, target,
    or media key that does not exist. A retry that reuses `event_id` inserts
    nothing. Rows older than the retention window, measured from this event's
    time, are deleted in the same transaction. Returns the event ID.
    """

    if actor_kind is ActorKind.UNATTRIBUTED:
        raise AuditEventRejected("New events are always attributed")
    action = check_event(event, actor_kind)
    for user_id in {event.actor, event.target_user} - {None}:
        if not db.scalar(select(exists().where(UserRow.id == user_id))):
            raise AuditEventRejected("Audit event names an account that does not exist")
    if event.media_key is not None and not db.scalar(
        select(exists().where(MediaItemRow.id == event.media_key))
    ):
        raise AuditEventRejected("Audit event names a media item that does not exist")
    stored_id = _uuid(event_id) if event_id is not None else str(uuid.uuid4())
    occurred_at = _stored_time(event.occurred_at)
    expire_events(db, now=event.occurred_at)
    db.execute(
        insert(AuditEventRow)
        .values(
            event_id=stored_id,
            occurred_at=occurred_at,
            action=action.value,
            outcome=event.outcome.value,
            actor_kind=actor_kind.value,
            actor_id=event.actor,
            credential=event.credential.value if event.credential is not None else None,
            target_user_id=event.target_user,
            media_key=event.media_key,
            resource=event.resource,
            request_id=_uuid(request_id) if request_id is not None else None,
            details_json=json.dumps(dict(event.details), sort_keys=True, separators=(",", ":")),
        )
        .on_conflict_do_nothing(index_elements=[AuditEventRow.event_id])
    )
    return stored_id


def record_event(
    db: Session,
    event: AuditEvent,
    *,
    actor_kind: ActorKind = ActorKind.USER,
    request_id: str | None = None,
    event_id: str | None = None,
) -> bool:
    """Store one event in its own transaction and report whether it was stored.

    Use it only when the caller commits nothing else, such as a refusal. An
    invalid event still raises `AuditEventRejected`.
    """

    try:
        stage_event(db, event, actor_kind=actor_kind, request_id=request_id, event_id=event_id)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        _logger.error("An audit event could not be stored; the request outcome is unchanged")
        return False
    return True


def retention_cutoff(now: datetime) -> datetime:
    """The stored time at or before which an event has expired."""

    return _stored_time(now - AUDIT_RETENTION)


def expire_events(db: Session, *, now: datetime) -> int:
    """Delete every event at or past the retention boundary; repeating it deletes nothing new."""

    return db.execute(
        delete(AuditEventRow).where(AuditEventRow.occurred_at <= retention_cutoff(now))
    ).rowcount


def run_retention(db: Session, *, now: datetime | None = None) -> int | None:
    """Expire events in their own transaction. Returns None when storage fails; rerun later."""

    try:
        removed = expire_events(db, now=now or datetime.now(timezone.utc))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        _logger.warning("Audit retention could not run; it retries at the next audit write")
        return None
    return removed


def _stored_time(value: datetime) -> datetime:
    """UTC without an offset, the form SQLite rows compare in."""

    if value.utcoffset() is None:
        raise AuditEventRejected("Audit time must be timezone-aware")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _uuid(value: str) -> str:
    try:
        canonical = str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError):
        raise AuditEventRejected("Audit identifiers must be UUIDs") from None
    if canonical != value:
        raise AuditEventRejected("Audit identifiers must be canonical UUIDs")
    return canonical
