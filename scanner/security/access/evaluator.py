"""The one decision function for HTTP handlers, services, workers, and serializers.

`decide` applies, in order: account status, target shape,
media, capability, target-account protection, and fresh authentication. The
order keeps existence hidden: a caller who may not see a target gets
`not_found` before any capability or protection detail.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from .api_shapes import AccessErrorCode
from .capability_ids import Capability
from .catalog import ACCOUNT_PROTECTION, CAPABILITIES
from .decisions import Allowed, Decision, Denied
from .fresh import FreshAuthentication
from .grants import PrincipalAccess
from .identity import AccountStatus, CredentialKind, UserId
from .records import MediaDenied
from .targets import (
    INSTANCE,
    AccountRef,
    Action,
    MediaRef,
    ResourceRef,
    Target,
    TargetMismatch,
    resolve_action,
    target_owner,
)
from .vocabulary import TARGET_RULES, Authentication, OwnerPower, Role
from .vocabulary import Target as TargetKind

_ACCEPTS: Mapping[TargetKind, Callable[[Target, UserId], bool]] = MappingProxyType(
    {
        TargetKind.INSTANCE: lambda target, _actor: target is INSTANCE,
        TargetKind.MEDIA: lambda target, _actor: isinstance(target, MediaRef),
        TargetKind.SELF: lambda target, actor: (
            isinstance(target, AccountRef) and target.user_id == actor
        ),
        TargetKind.OWN_RESOURCE: lambda target, actor: (
            isinstance(target, ResourceRef) and target_owner(target) == actor
        ),
        TargetKind.OTHER_RESOURCE: lambda target, actor: (
            isinstance(target, ResourceRef) and target_owner(target) != actor
        ),
        TargetKind.OTHER_ACCOUNT: lambda target, actor: (
            isinstance(target, AccountRef) and target.user_id != actor
        ),
        TargetKind.OTHER_ACCOUNT_RESOURCE: lambda target, actor: (
            isinstance(target, ResourceRef)
            and target.owner is not None
            and target.owner.user_id != actor
        ),
        TargetKind.SCOPED_RECORDS: lambda target, _actor: isinstance(target, ResourceRef),
    }
)
"""Which resolved targets each capability target kind can act on."""


def _state_denial(access: PrincipalAccess) -> AccessErrorCode | None:
    if access.principal.account.status is AccountStatus.DISABLED:
        return AccessErrorCode.ACCOUNT_DISABLED
    return None


def _existence_visible(access: PrincipalAccess, target: Target) -> bool:
    """Whether a denial may reveal that the target exists; otherwise it answers `not_found`."""

    if isinstance(target, AccountRef):
        return Capability.ACCOUNTS_LIST in access.capabilities
    if isinstance(target, ResourceRef | MediaRef):
        return target.existence_visible
    return True


def _protected_account(access: PrincipalAccess, target: Target) -> bool:
    if isinstance(target, AccountRef):
        account = target
    elif isinstance(target, ResourceRef) and target.owner is not None:
        account = target.owner
    else:
        return False
    if account.user_id == access.principal.user_id:
        return False
    requirement = ACCOUNT_PROTECTION[account.account_class]
    if requirement is None:
        return False
    if isinstance(requirement, OwnerPower):
        return access.principal.account.role is not Role.OWNER
    return requirement not in access.capabilities


def decide(
    access: PrincipalAccess,
    action: Action,
    target: Target = INSTANCE,
    *,
    fresh: FreshAuthentication | None = None,
) -> Decision:
    """Decide one action on one resolved target for a live principal.

    `action` is a capability or an own/other pair that the target resolves.
    `fresh` is a proof from `verify_reauthentication`, required only by
    fresh-interactive capabilities. Raises `TargetMismatch` when the target
    cannot carry the capability, which is a caller defect.
    """

    actor = access.principal.user_id
    capability = resolve_action(action, actor, target)
    state = _state_denial(access)
    if state is not None:
        return Denied(state)
    spec = CAPABILITIES[capability]
    if not _ACCEPTS[spec.target](target, actor):
        raise TargetMismatch(f"{capability} cannot act on {target!r}")
    rule = TARGET_RULES[spec.target]
    owner = access.principal.account.role is Role.OWNER
    media = target.media if isinstance(target, ResourceRef | MediaRef) else None
    if rule.media_scoped and not owner and isinstance(media, MediaDenied):
        visible = _existence_visible(access, target)
        return Denied(AccessErrorCode.MEDIA_UNAVAILABLE if visible else AccessErrorCode.NOT_FOUND)
    others = target_owner(target) != actor and isinstance(target, AccountRef | ResourceRef)
    if capability not in access.capabilities:
        hidden = others and not _existence_visible(access, target)
        return Denied(AccessErrorCode.NOT_FOUND if hidden else AccessErrorCode.CAPABILITY_DENIED)
    if rule.account_protected and _protected_account(access, target):
        visible = _existence_visible(access, target)
        return Denied(AccessErrorCode.PROTECTED_ACCOUNT if visible else AccessErrorCode.NOT_FOUND)
    if spec.authentication is Authentication.FRESH_INTERACTIVE:
        if access.principal.credential is not CredentialKind.SESSION:
            return Denied(AccessErrorCode.INTERACTIVE_SESSION_REQUIRED)
        if fresh is None:
            return Denied(AccessErrorCode.REAUTHENTICATION_REQUIRED)
        if not fresh.proves(access):
            return Denied(AccessErrorCode.REAUTHENTICATION_FAILED)
    return Allowed(capability)


def attemptable(access: PrincipalAccess) -> frozenset[Capability]:
    """Capabilities this principal may attempt before target narrowing, for client payloads.

    Excludes everything while the account state blocks it and, for API keys,
    every fresh-interactive capability.
    """

    api_key = access.principal.credential is CredentialKind.API_KEY
    return frozenset(
        capability
        for capability in access.capabilities
        if _state_denial(access) is None
        and not (
            api_key and CAPABILITIES[capability].authentication is Authentication.FRESH_INTERACTIVE
        )
    )
