"""Ownership transfer: the one path that moves the owner role.

`transfer_ownership` runs under the caller's write lock (`account_operations.run`)
and stages both role changes in that transaction, so the instance never has two
owners or none. The demotion is fenced on the caller still being the owner, and the
lock keeps the successor's eligibility read current; the partial unique owner index
and `ck_users_owner_state` remain the storage guards beneath both.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from scanner.persistence.repository import filter_enforcement
from scanner.persistence.rows.db_auth import UserProviderIdentityRow, UserRow
from scanner.persistence.rows.db_policies import UserFilterPreferenceRow
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.identity import (
    AccountStatus,
    CredentialKind,
    ProviderLinkState,
    UserId,
)
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.access.vocabulary import Role
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import stage_event
from scanner.settings.filter_policy.updates import FilterPolicyUpdate


class SuccessorRefusal(StrEnum):
    """Why an account cannot become the owner."""

    DISABLED = "successor_disabled"
    CREDENTIALS_REQUIRED = "successor_credentials_required"
    """No way to prove itself: no Jellyfin link on the configured server."""


class SuccessorIneligible(Exception):
    def __init__(self, reason: SuccessorRefusal) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _refusal(status: str, link_state: str | None) -> SuccessorRefusal | None:
    if status != AccountStatus.ACTIVE.value:
        return SuccessorRefusal.DISABLED
    if link_state in (None, ProviderLinkState.SERVER_REPLACED.value):
        return SuccessorRefusal.CREDENTIALS_REQUIRED
    return None


def _successor(db: Session, user_id: int) -> tuple[str, SuccessorRefusal | None]:
    role, status, link_state = db.execute(
        select(UserRow.role, UserRow.status, UserProviderIdentityRow.link_state)
        .outerjoin(UserProviderIdentityRow, UserProviderIdentityRow.user_id == UserRow.id)
        .where(UserRow.id == user_id)
    ).one()
    return role, _refusal(status, link_state)


def successor_refusal(db: Session, user_id: int) -> SuccessorRefusal | None:
    """Why `user_id` cannot become the owner now, or None when it can."""
    return _successor(db, user_id)[1]


def transfer_ownership(db: Session, owner_id: int, successor_id: int, *, now: datetime) -> Role:
    """Make `successor_id` the owner and `owner_id` an admin; return the successor's old role.

    Stages both changes without committing. Raises `SuccessorIneligible` for an
    account that is disabled or cannot prove itself afterwards: it needs a Jellyfin
    link to the configured server, whose password then confirms its sensitive
    actions. Raises `AccessDenied`
    (`capability_denied`) when `owner_id` no longer holds the owner role. The former
    owner's admin role is explicit, so a linked former owner does not follow
    Jellyfin's role mapping. Both accounts' permission revisions move. Any enforced
    policy on the successor is cleared and audited in the same transaction.
    """

    role, refusal = _successor(db, successor_id)
    if refusal is not None:
        raise SuccessorIneligible(refusal)
    stored = now.astimezone(timezone.utc).replace(tzinfo=None)
    moved = {"updated_at": stored, "permission_revision": UserRow.permission_revision + 1}
    demoted = db.execute(
        update(UserRow)
        .where(UserRow.id == owner_id, UserRow.role == Role.OWNER.value)
        .values(role=Role.ADMIN.value, **moved)
    ).rowcount
    if demoted != 1:
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    db.execute(
        update(UserRow).where(UserRow.id == successor_id).values(role=Role.OWNER.value, **moved)
    )
    revision = (
        db.scalar(
            select(UserFilterPreferenceRow.policy_revision).where(
                UserFilterPreferenceRow.user_id == successor_id
            )
        )
        or 0
    )
    cleared = filter_enforcement.set_enforcement(
        db,
        successor_id,
        FilterPolicyUpdate(policy_revision=revision, rules=[], severities=[]),
        now=now,
    )
    if cleared is not None:
        stage_event(
            db,
            AuditEvent(
                now,
                UserId(owner_id),
                CredentialKind.SESSION,
                AuditAction.FILTER_POLICY_CHANGE,
                AuditOutcome.SUCCEEDED,
                target_user=UserId(successor_id),
                details={
                    "cause": "ownership_transfer",
                    "policy_revision": cleared.policy_revision,
                    "rules": 0,
                    "severities": 0,
                },
            ),
        )
    return Role(role)
