"""The capability row type and the guards that keep owner powers nondelegable."""

from __future__ import annotations

from dataclasses import dataclass, field

from .capability_ids import Capability
from .vocabulary import Authentication, OwnerPower, Role, Target


@dataclass(frozen=True)
class CapabilitySpec:
    """One individually testable action from the permission matrix.

    `granted` lists the delegated roles that hold the capability by default.
    The owner holds every capability. `owner_power` names the nondelegable
    power a capability belongs to; such capabilities are never granted to
    another role and never accept an override. `role_fixed` keeps a capability
    with exactly its `granted` roles: no role default or override changes who
    holds it. `matrix_rows` cites the exact
    matrix action text; a nondelegable power from the matrix's closing
    paragraph may have none.
    """

    capability: Capability
    target: Target
    granted: frozenset[Role]
    matrix_rows: tuple[str, ...]
    authentication: Authentication = Authentication.ANY
    owner_power: OwnerPower | None = field(default=None)
    role_fixed: bool = False

    def __post_init__(self) -> None:
        if Role.OWNER in self.granted:
            raise ValueError(f"{self.capability}: the owner is implicit, not a granted role")
        if self.owner_power is not None and self.granted:
            raise ValueError(f"{self.capability}: an owner power cannot be a role default")
        if self.role_fixed and self.owner_power is not None:
            raise ValueError(f"{self.capability}: an owner power is already fixed to the owner")
        if not self.matrix_rows and self.owner_power is None:
            raise ValueError(f"{self.capability}: a delegable capability needs a matrix row")

    @property
    def delegable(self) -> bool:
        return self.owner_power is None and not self.role_fixed


def spec(
    capability: Capability,
    target: Target,
    granted: frozenset[Role],
    *rows: str,
    authentication: Authentication = Authentication.ANY,
    owner_power: OwnerPower | None = None,
    role_fixed: bool = False,
) -> CapabilitySpec:
    """Build one row with the matrix citations as trailing arguments."""

    return CapabilitySpec(
        capability, target, granted, rows, authentication, owner_power, role_fixed
    )
