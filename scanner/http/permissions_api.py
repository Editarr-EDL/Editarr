"""Owner-only permission management: individual overrides and role defaults.

Overrides and role defaults accept only delegable capabilities, so no request
can hand an owner power to another account. Both changes need the owner's
password in the request and move the affected accounts' permission revision.
Roles are assigned through `/api/users/{user_id}/role`.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from scanner.security import account_permissions as permissions
from scanner.security.access.api_shapes import AccessErrorCode, Reauthentication
from scanner.security.access.authorization import may_attempt
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.catalog import CAPABILITIES, DELEGABLE
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.identity import CredentialKind
from scanner.security.access.load import load_access
from scanner.security.access.overrides import parse_override, parse_role_default
from scanner.security.access.targets import INSTANCE, AccountRef
from scanner.security.access.vocabulary import Role
from scanner.security.audit.actions import AuditAction

from . import account_operations as operations
from .users_access import Caller, Refusal, caller, respond

router = APIRouter(tags=["permissions"])


class Overrides(BaseModel):
    role: Role
    permission_revision: int
    overrides: dict[str, Literal["allow", "deny"]]
    effective: list[str] = Field(description="Capabilities the account holds now")


class OverridesChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrides: dict[str, str] = Field(
        min_length=1, max_length=len(DELEGABLE), description="Capability to allow, deny, or inherit"
    )
    reauthentication: Reauthentication | None = None


class RoleDefault(BaseModel):
    capability: str
    granted: bool
    catalog_default: bool
    explicit: bool


class RoleDefaults(BaseModel):
    admin: list[RoleDefault]
    user: list[RoleDefault]


class RoleDefaultsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    defaults: dict[str, bool | None] = Field(
        min_length=1,
        max_length=len(DELEGABLE),
        description="Capability to grant (true), withhold (false), or reset to the catalog (null)",
    )
    reauthentication: Reauthentication | None = None


def _overrides(caller: Caller, target: AccountRef) -> Overrides:
    if not may_attempt(caller.access, C.PERMISSIONS_OVERRIDES_EDIT, target):
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    access = load_access(caller.db, target.user_id, CredentialKind.SESSION)
    if access is None:
        raise Refusal(404, "not_found", "Account not found")
    stored = permissions.read_overrides(caller.db, target.user_id)
    return Overrides(
        role=access.principal.account.role,
        permission_revision=access.principal.permission_revision,
        overrides={capability.value: effect.value for capability, effect in stored.items()},
        effective=sorted(capability.value for capability in access.capabilities),
    )


@router.get("/api/users/{user_id}/permissions", response_model=Overrides)
def read_overrides(user_id: int, caller: Caller = Depends(caller)):
    """Another account's role, stored overrides, and effective capabilities (owner only)."""

    return respond(
        caller, lambda: _overrides(caller, caller.other(user_id, C.PERMISSIONS_OVERRIDES_EDIT))
    )


@router.patch("/api/users/{user_id}/permissions", response_model=Overrides)
def change_overrides(user_id: int, body: OverridesChange, caller: Caller = Depends(caller)):
    """Allow, deny, or inherit the listed capabilities for another account (owner only)."""

    def change() -> Overrides:
        target = caller.other(
            user_id, C.PERMISSIONS_OVERRIDES_EDIT, audited=AuditAction.PERMISSIONS_CHANGE
        )
        parsed = [parse_override(name, effect) for name, effect in body.overrides.items()]
        operation = operations.Operation(
            C.PERMISSIONS_OVERRIDES_EDIT,
            AuditAction.PERMISSIONS_CHANGE,
            lambda caller, target, _fresh: {
                "change": "overrides",
                "changed": permissions.change_overrides(
                    caller.db, target.user_id, parsed, now=caller.now
                ),
            },
        )
        operations.run(caller, operation, target, operations.Proofs(caller, body.reauthentication))
        return _overrides(caller, target)

    return respond(caller, change)


def _defaults(caller: Caller) -> RoleDefaults:
    if not may_attempt(caller.access, C.ROLES_DEFAULTS_EDIT):
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    stored = permissions.read_role_defaults(caller.db)

    def rows(role: Role) -> list[RoleDefault]:
        return [
            RoleDefault(
                capability=capability.value,
                granted=stored.get((role, capability), role in CAPABILITIES[capability].granted),
                catalog_default=role in CAPABILITIES[capability].granted,
                explicit=(role, capability) in stored,
            )
            for capability in sorted(DELEGABLE, key=lambda capability: capability.value)
        ]

    return RoleDefaults(admin=rows(Role.ADMIN), user=rows(Role.USER))


@router.get("/api/roles/defaults", response_model=RoleDefaults)
def read_role_defaults(caller: Caller = Depends(caller)):
    """Every delegable capability's default for admins and users (owner only)."""

    return respond(caller, lambda: _defaults(caller))


@router.patch("/api/roles/{role}/defaults", response_model=RoleDefaults)
def change_role_defaults(
    role: Literal["admin", "user"], body: RoleDefaultsChange, caller: Caller = Depends(caller)
):
    """Change the listed defaults; accounts of that role without an override follow live."""

    def change() -> RoleDefaults:
        proofs = operations.Proofs(caller, body.reauthentication)
        fresh = operations.authorized(
            caller, proofs, C.ROLES_DEFAULTS_EDIT, INSTANCE, AuditAction.PERMISSIONS_CHANGE
        )
        changes = {}
        for name, granted in body.defaults.items():
            parsed = parse_role_default(role, name, True if granted is None else granted)
            changes[parsed.capability] = None if granted is None else parsed
        with operations.locked(
            caller, C.ROLES_DEFAULTS_EDIT, INSTANCE, AuditAction.PERMISSIONS_CHANGE, fresh=fresh
        ):
            changed = permissions.change_role_defaults(
                caller.db, Role(role), changes, now=caller.now
            )
            caller.stage(
                AuditAction.PERMISSIONS_CHANGE,
                None,
                {"change": "role_defaults", "role": role, "changed": changed},
            )
            caller.db.commit()
        return _defaults(caller)

    return respond(caller, change)
