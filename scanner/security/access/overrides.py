"""Parse role-default changes and individual overrides at the storage/API boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .capability_ids import Capability
from .catalog import DELEGABLE, ROLE_FIXED
from .vocabulary import DELEGATED_ROLES, Role


class OverrideEffect(StrEnum):
    INHERIT = "inherit"
    ALLOW = "allow"
    DENY = "deny"


class OverrideRejected(ValueError):
    """The requested default or override can never be stored."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PermissionOverride:
    """One user's explicit effect for one delegable capability.

    `INHERIT` is the absence of a stored row; the role default then applies live.
    """

    capability: Capability
    effect: OverrideEffect


@dataclass(frozen=True)
class RoleDefault:
    role: Role
    capability: Capability
    granted: bool


def _delegable(raw: str) -> Capability:
    try:
        capability = Capability(raw)
    except ValueError:
        raise OverrideRejected("unknown_capability", "Capability is not recognized") from None
    if capability in ROLE_FIXED:
        raise OverrideRejected("role_fixed_capability", "This capability belongs to its role")
    if capability not in DELEGABLE:
        raise OverrideRejected("owner_only_capability", "Owner-only powers cannot be delegated")
    return capability


def parse_override(capability: str, effect: str) -> PermissionOverride:
    """Validate an individual override request; owner powers are never accepted."""

    try:
        parsed_effect = OverrideEffect(effect)
    except ValueError:
        raise OverrideRejected("invalid_override_effect", "Override effect is invalid") from None
    return PermissionOverride(_delegable(capability), parsed_effect)


def parse_role_default(role: str, capability: str, granted: bool) -> RoleDefault:
    """Validate a role-default change; only user and admin defaults are configurable."""

    try:
        parsed_role = Role(role)
    except ValueError:
        raise OverrideRejected("invalid_role", "Role is invalid") from None
    if parsed_role not in DELEGATED_ROLES:
        raise OverrideRejected("owner_role_fixed", "The owner always has full access")
    if not isinstance(granted, bool):
        raise OverrideRejected("invalid_role_default", "A role default must be true or false")
    return RoleDefault(parsed_role, _delegable(capability), granted)
