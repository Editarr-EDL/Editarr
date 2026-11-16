"""Live role defaults and individual overrides resolved into one account's capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .capability_ids import Capability
from .catalog import CAPABILITIES, DELEGABLE, ROLE_FIXED
from .identity import CredentialKind, Principal
from .overrides import OverrideEffect
from .vocabulary import Role

ALL_CAPABILITIES: frozenset[Capability] = frozenset(CAPABILITIES)


@dataclass(frozen=True)
class GrantInputs:
    """The stored permission state of one account, read once per request or job run.

    `role_defaults` holds only the changed defaults of the account's role.
    `overrides` holds explicit `ALLOW` or `DENY` effects; inherit is absence.
    `unreadable` names delegable capabilities whose stored default or override
    could not be parsed; they are denied rather than guessed.
    """

    role: Role
    role_defaults: Mapping[Capability, bool] = field(default_factory=dict)
    overrides: Mapping[Capability, OverrideEffect] = field(default_factory=dict)
    unreadable: frozenset[Capability] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_defaults", MappingProxyType(dict(self.role_defaults)))
        object.__setattr__(self, "overrides", MappingProxyType(dict(self.overrides)))
        if OverrideEffect.INHERIT in self.overrides.values():
            raise ValueError("Inherit is the absence of an override, not a stored effect")


def effective_capabilities(inputs: GrantInputs) -> frozenset[Capability]:
    """Return what an account holds before account state, target, and freshness checks.

    The owner holds every capability whatever is stored. Nobody else ever holds
    an owner power, even through a corrupt stored row. A role-fixed capability
    follows only the catalog default. For delegable capabilities an override
    wins, then a role-default change, then the catalog default.
    """

    if inputs.role is Role.OWNER:
        return ALL_CAPABILITIES
    held = {
        capability for capability in ROLE_FIXED if inputs.role in CAPABILITIES[capability].granted
    }
    for capability in DELEGABLE - inputs.unreadable:
        effect = inputs.overrides.get(capability)
        if effect is not None:
            granted = effect is OverrideEffect.ALLOW
        else:
            granted = inputs.role_defaults.get(
                capability, inputs.role in CAPABILITIES[capability].granted
            )
        if granted:
            held.add(capability)
    return frozenset(held)


@dataclass(frozen=True)
class PrincipalAccess:
    """A request or job principal together with its live effective capabilities.

    `request_access` builds a new one for every request, and a fresh proof
    binds to that object. `session_id` identifies the browser session behind
    a request. API keys never have one, and neither does a durable job, so a
    job can never satisfy fresh interactive authentication.
    """

    principal: Principal
    capabilities: frozenset[Capability]
    session_id: int | None = None

    def __post_init__(self) -> None:
        if self.session_id is not None and self.principal.credential is not CredentialKind.SESSION:
            raise ValueError("Only a session principal carries a session ID")
