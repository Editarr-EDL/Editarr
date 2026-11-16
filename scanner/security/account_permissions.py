"""Owner-only permission writes: roles, individual overrides, and role defaults.

Each write bumps `permission_revision` for every account whose effective
permissions may change, in the caller's transaction, so clients and durable
jobs notice. Callers parse requests through `access.overrides` first, so an
owner power never reaches storage.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from sqlalchemy import ColumnElement, and_, delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from scanner.persistence.rows.db_auth import (
    RoleCapabilityDefaultRow,
    UserCapabilityOverrideRow,
    UserRow,
)
from scanner.security.access.capability_ids import Capability
from scanner.security.access.overrides import OverrideEffect, PermissionOverride, RoleDefault
from scanner.security.access.vocabulary import DELEGATED_ROLES, Role


def _stored(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(tzinfo=None)


def _bump(db: Session, *conditions) -> None:
    db.execute(
        update(UserRow)
        .where(*conditions)
        .values(permission_revision=UserRow.permission_revision + 1)
    )


def assign_role(db: Session, user_id: int, role: Role, *, now: datetime) -> bool:
    """Make another account an admin or ordinary user; return whether its role changed.

    Ownership moves only through ownership transfer.
    """

    if role not in DELEGATED_ROLES:
        raise ValueError("Only admin and user roles are assignable")
    changed = db.execute(
        update(UserRow)
        .where(UserRow.id == user_id, UserRow.role != Role.OWNER.value, UserRow.role != role.value)
        .values(
            role=role.value,
            updated_at=_stored(now),
            permission_revision=UserRow.permission_revision + 1,
        )
    ).rowcount
    return changed > 0


def applied_override() -> ColumnElement[bool]:
    """Stored override rows that still mean something.

    A row naming a removed capability, or a corrupt effect, stays stored but is skipped.
    """

    return and_(
        UserCapabilityOverrideRow.capability.in_([capability.value for capability in Capability]),
        UserCapabilityOverrideRow.effect.in_([OverrideEffect.ALLOW, OverrideEffect.DENY]),
    )


def read_overrides(db: Session, user_id: int) -> dict[Capability, OverrideEffect]:
    """The account's stored allow and deny overrides."""

    rows = db.execute(
        select(UserCapabilityOverrideRow.capability, UserCapabilityOverrideRow.effect).where(
            UserCapabilityOverrideRow.user_id == user_id, applied_override()
        )
    ).all()
    return {Capability(row.capability): OverrideEffect(row.effect) for row in rows}


def change_overrides(
    db: Session, user_id: int, overrides: Iterable[PermissionOverride], *, now: datetime
) -> int:
    """Apply the listed overrides; `inherit` removes one. Returns how many rows changed."""

    changed = 0
    for override in overrides:
        match = (
            UserCapabilityOverrideRow.user_id == user_id,
            UserCapabilityOverrideRow.capability == override.capability.value,
        )
        if override.effect is OverrideEffect.INHERIT:
            changed += db.execute(delete(UserCapabilityOverrideRow).where(*match)).rowcount
            continue
        current = db.scalar(select(UserCapabilityOverrideRow.effect).where(*match))
        if current == override.effect.value:
            continue
        values = {"effect": override.effect.value, "updated_at": _stored(now)}
        db.execute(
            sqlite_insert(UserCapabilityOverrideRow)
            .values(user_id=user_id, capability=override.capability.value, **values)
            .on_conflict_do_update(
                index_elements=[
                    UserCapabilityOverrideRow.user_id,
                    UserCapabilityOverrideRow.capability,
                ],
                set_=values,
            )
        )
        changed += 1
    if changed:
        _bump(db, UserRow.id == user_id)
    return changed


def read_role_defaults(db: Session) -> dict[tuple[Role, Capability], bool]:
    """Stored role-default changes; a missing pair means the catalog default."""

    known = {capability.value for capability in Capability}
    return {
        (Role(row.role), Capability(row.capability)): bool(row.granted)
        for row in db.execute(select(RoleCapabilityDefaultRow)).scalars()
        if row.capability in known and row.role in {role.value for role in DELEGATED_ROLES}
    }


def change_role_defaults(
    db: Session,
    role: Role,
    changes: Mapping[Capability, RoleDefault | None],
    *,
    now: datetime,
) -> int:
    """Store or reset role defaults; `None` returns a capability to its catalog default.

    Every account of `role` inherits the change live, so each one's revision moves.
    Returns how many rows changed.
    """

    changed = 0
    for capability, default in changes.items():
        match = (
            RoleCapabilityDefaultRow.role == role.value,
            RoleCapabilityDefaultRow.capability == capability.value,
        )
        current = db.scalar(select(RoleCapabilityDefaultRow.granted).where(*match))
        if default is None:
            changed += db.execute(delete(RoleCapabilityDefaultRow).where(*match)).rowcount
            continue
        if current is not None and bool(current) is default.granted:
            continue
        values = {"granted": default.granted, "updated_at": _stored(now)}
        db.execute(
            sqlite_insert(RoleCapabilityDefaultRow)
            .values(role=role.value, capability=capability.value, **values)
            .on_conflict_do_update(
                index_elements=[RoleCapabilityDefaultRow.role, RoleCapabilityDefaultRow.capability],
                set_=values,
            )
        )
        changed += 1
    if changed:
        _bump(db, UserRow.role == role.value)
    return changed
