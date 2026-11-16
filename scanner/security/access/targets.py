"""Resolved authorization targets and own/other action pairs.

Handlers, services, workers, and serializers resolve a target before asking
for a decision: the instance, an account, a resource with its current owner,
or a media item. Media-scoped targets must state their media decision, or
`NO_MEDIA` when the resource has none, so a forgotten lookup cannot pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from .capability_ids import Capability
from .catalog import CAPABILITIES
from .identity import UserId
from .records import MediaDecision
from .vocabulary import TARGET_RULES, AccountClass, Facet, Role

_ACCOUNT_CLASS: Mapping[Role, AccountClass] = MappingProxyType(
    {
        Role.USER: AccountClass.ORDINARY,
        Role.ADMIN: AccountClass.ADMIN,
        Role.OWNER: AccountClass.OWNER,
    }
)


class _Instance(Enum):
    INSTANCE = "instance"


INSTANCE = _Instance.INSTANCE
"""The instance itself, for settings, operations, and creation actions."""


class _NoMedia(Enum):
    NO_MEDIA = "no_media"


NO_MEDIA = _NoMedia.NO_MEDIA
"""A resource with no media item, stated explicitly."""


@dataclass(frozen=True)
class AccountRef:
    """An account as currently stored: its immutable ID and current role."""

    user_id: UserId
    role: Role

    @property
    def account_class(self) -> AccountClass:
        return _ACCOUNT_CLASS[self.role]


@dataclass(frozen=True)
class ResourceRef:
    """A review, scan, job, export, publication, receipt, or audit record.

    `owner` is the current owner, never the historical author; `None` means a
    system record. `existence_visible` is true only when the caller already
    lists this resource, so a denial may say forbidden instead of not found.
    """

    owner: AccountRef | None
    media: MediaDecision | _NoMedia
    existence_visible: bool = False


@dataclass(frozen=True)
class MediaRef:
    media: MediaDecision
    existence_visible: bool = False


Target = _Instance | AccountRef | ResourceRef | MediaRef


class TargetMismatch(TypeError):
    """A caller passed a target that the capability cannot act on. This is a code defect."""


@dataclass(frozen=True)
class ActionPair:
    """An own capability and its other-account counterpart, chosen by the resolved target."""

    own: Capability
    other: Capability

    def __post_init__(self) -> None:
        if TARGET_RULES[CAPABILITIES[self.own].target].facet is not Facet.OWN:
            raise ValueError(f"{self.own} is not an own capability")
        if TARGET_RULES[CAPABILITIES[self.other].target].facet is not Facet.OTHER:
            raise ValueError(f"{self.other} is not an other capability")


Action = Capability | ActionPair


def target_owner(target: Target) -> UserId | None:
    """The account a target belongs to, or `None` for the instance, media, and system records."""

    if isinstance(target, AccountRef):
        return target.user_id
    if isinstance(target, ResourceRef) and target.owner is not None:
        return target.owner.user_id
    return None


def resolve_action(action: Action, actor: UserId, target: Target) -> Capability:
    """Pick the own capability when the target belongs to the actor, else the other one."""

    if isinstance(action, ActionPair):
        return action.own if target_owner(target) == actor else action.other
    return action
