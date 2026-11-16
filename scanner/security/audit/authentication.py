"""Audit events for sign-in, setup, credential, session, and API-key changes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from scanner.security.access.identity import CredentialKind, UserId
from scanner.security.access.records import AuditEvent, AuditOutcome

from .actions import ActorKind, AuditAction
from .store import record_event, stage_event

SignInMethod = Literal["password", "quick_connect"]
"""How a Jellyfin user proved itself at sign-in."""


def _at(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _method(method: SignInMethod | None) -> dict[str, str]:
    return {} if method is None else {"method": method}


def setup_completed(db: Session, user_id: int, *, now: datetime | None = None) -> None:
    """Stage owner creation by the new owner's first session."""

    event = AuditEvent(
        _at(now),
        UserId(user_id),
        CredentialKind.SESSION,
        AuditAction.SETUP,
        AuditOutcome.SUCCEEDED,
        target_user=UserId(user_id),
    )
    stage_event(db, event, actor_kind=ActorKind.USER)


def setup_failed(db: Session, *, now: datetime | None = None) -> None:
    event = AuditEvent(
        _at(now),
        None,
        None,
        AuditAction.SETUP,
        AuditOutcome.FAILED,
        details={"reason": "already_configured"},
    )
    record_event(db, event, actor_kind=ActorKind.ANONYMOUS)


def signed_in(
    db: Session,
    user_id: int,
    *,
    remember_me: bool,
    now: datetime | None,
    method: SignInMethod | None = None,
) -> None:
    """Stage a successful sign-in in the transaction that issues its session."""

    event = AuditEvent(
        _at(now),
        UserId(user_id),
        CredentialKind.SESSION,
        AuditAction.SIGN_IN,
        AuditOutcome.SUCCEEDED,
        target_user=UserId(user_id),
        details={"remember_me": remember_me} | _method(method),
    )
    stage_event(db, event)


def sign_in_refused(
    db: Session,
    outcome: AuditOutcome,
    reason: str,
    *,
    target: int | None,
    now: datetime | None,
    method: SignInMethod | None = None,
) -> None:
    """Record a refused sign-in. `target` is the matched account, never the typed name."""

    event = AuditEvent(
        _at(now),
        None,
        None,
        AuditAction.SIGN_IN,
        outcome,
        target_user=UserId(target) if target is not None else None,
        details={"reason": reason} | _method(method),
    )
    record_event(db, event, actor_kind=ActorKind.ANONYMOUS)


def sessions_revoked_by_key_change(db: Session, count: int, *, now: datetime | None = None) -> None:
    """Stage startup's revocation of sessions bound to a replaced CSRF key with that revocation."""

    event = AuditEvent(
        _at(now),
        None,
        None,
        AuditAction.SESSIONS_REVOKE,
        AuditOutcome.SUCCEEDED,
        details={"sessions_revoked": count, "reason": "csrf_key_changed"},
    )
    stage_event(db, event, actor_kind=ActorKind.SYSTEM)


def api_key_changed(
    db: Session,
    user_id: int,
    credential: CredentialKind,
    *,
    target: int,
    issued: bool,
    had_key: bool,
    now: datetime | None = None,
) -> None:
    """Stage issuing (or replacing) or revoking `target`'s API key by `user_id`."""

    action, details = (
        (AuditAction.API_KEY_ISSUE, {"replaced": had_key})
        if issued
        else (AuditAction.API_KEY_REVOKE, {"revoked": had_key})
    )
    event = AuditEvent(
        _at(now),
        UserId(user_id),
        credential,
        action,
        AuditOutcome.SUCCEEDED,
        target_user=UserId(target),
        details=details,
    )
    stage_event(db, event)


def api_key_revealed(
    db: Session, user_id: int, credential: CredentialKind, *, target: int, now: datetime
) -> None:
    """Stage that `user_id` revealed `target`'s key; the caller commits before showing it."""

    event = AuditEvent(
        now,
        UserId(user_id),
        credential,
        AuditAction.API_KEY_REVEAL,
        AuditOutcome.SUCCEEDED,
        target_user=UserId(target),
    )
    stage_event(db, event)


def api_key_refused(
    db: Session,
    action: AuditAction,
    user_id: int,
    credential: CredentialKind,
    *,
    target: int,
    reason: str,
    now: datetime,
) -> None:
    """Store a refused key action; a wrong confirmation password counts as failed."""

    outcome = AuditOutcome.FAILED if reason == "reauthentication_failed" else AuditOutcome.DENIED
    event = AuditEvent(
        now,
        UserId(user_id),
        credential,
        action,
        outcome,
        target_user=UserId(target),
        details={"reason": reason},
    )
    record_event(db, event)
