"""The owner account: who it is, whether setup still awaits one, and what it adopts.

Owner rows are found by role, never by a fixed ID.
"""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from scanner.db import ApiKeyRow, UserRow
from scanner.persistence.repository.activity_ownership import (
    ActivityOwner,
    assign_awaiting_activity,
)
from scanner.security.access.vocabulary import Role


class AdministratorNotFoundError(RuntimeError):
    """Raised when a session is issued without the required owner."""


def owner_id(db: Session) -> int | None:
    """Return the current owner's stable user ID, or ``None`` before owner setup."""

    return db.scalar(select(UserRow.id).where(UserRow.role == Role.OWNER.value))


def setup_required(db: Session) -> bool:
    """Whether first-run setup still awaits an owner; the absence of one is authoritative."""

    return owner_id(db) is None


def adopt_unowned(db: Session, owner_id: int) -> None:
    """Stage giving the new owner every API key and activity record awaiting an owner."""

    revision = db.scalar(select(UserRow.permission_revision).where(UserRow.id == owner_id))
    db.execute(update(ApiKeyRow).where(ApiKeyRow.user_id.is_(None)).values(user_id=owner_id))
    assign_awaiting_activity(db, ActivityOwner(owner_id, revision))
