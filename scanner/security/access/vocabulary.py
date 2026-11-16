"""Roles, targets, authentication strength and owner powers used by capability rows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Role(StrEnum):
    USER = "user"
    ADMIN = "admin"
    OWNER = "owner"


DELEGATED_ROLES = (Role.USER, Role.ADMIN)
"""Roles whose defaults are configurable. The owner always has full access."""

NOBODY: frozenset[Role] = frozenset()
ADMINS: frozenset[Role] = frozenset({Role.ADMIN})
EVERYONE: frozenset[Role] = frozenset({Role.USER, Role.ADMIN})


class Facet(StrEnum):
    """Which side of an own/other matrix cell a capability represents."""

    OWN = "own"
    OTHER = "other"
    ANY = "any"


class Target(StrEnum):
    INSTANCE = "instance"
    MEDIA = "media"
    SELF = "self"
    OWN_RESOURCE = "own_resource"
    OTHER_RESOURCE = "other_resource"
    OTHER_ACCOUNT = "other_account"
    OTHER_ACCOUNT_RESOURCE = "other_account_resource"
    SCOPED_RECORDS = "scoped_records"


@dataclass(frozen=True)
class TargetRule:
    """How the evaluator must narrow a capability for one target kind.

    `media_scoped` means the caller's media decision must allow the target's
    media. `account_protected` means the target account's class must pass
    `ACCOUNT_PROTECTION`. Resources are reviews, scans, jobs, exports,
    publications and import receipts; their owner is the current owner, not
    the historical author.
    """

    facet: Facet
    media_scoped: bool
    account_protected: bool


TARGET_RULES: Mapping[Target, TargetRule] = MappingProxyType(
    {
        Target.INSTANCE: TargetRule(Facet.ANY, media_scoped=False, account_protected=False),
        Target.MEDIA: TargetRule(Facet.ANY, media_scoped=True, account_protected=False),
        Target.SELF: TargetRule(Facet.OWN, media_scoped=False, account_protected=False),
        Target.OWN_RESOURCE: TargetRule(Facet.OWN, media_scoped=True, account_protected=False),
        Target.OTHER_RESOURCE: TargetRule(Facet.OTHER, media_scoped=True, account_protected=False),
        Target.OTHER_ACCOUNT: TargetRule(Facet.OTHER, media_scoped=False, account_protected=True),
        Target.OTHER_ACCOUNT_RESOURCE: TargetRule(
            Facet.OTHER, media_scoped=True, account_protected=True
        ),
        # Rows are filtered to accounts and media the caller may already act on.
        Target.SCOPED_RECORDS: TargetRule(Facet.ANY, media_scoped=True, account_protected=True),
    }
)


class Authentication(StrEnum):
    ANY = "any"
    """A session or the account's API key."""

    FRESH_INTERACTIVE = "fresh_interactive"
    """A browser session plus a reauthentication proof verified in the same request.

    API keys never satisfy this, even when the account otherwise holds the
    capability.
    """


class OwnerPower(StrEnum):
    """The permanently nondelegable owner powers."""

    TRANSFER_OWNERSHIP = "transfer_ownership"
    MANAGE_OWNER_ACCOUNT = "manage_owner_account"
    EDIT_ROLE_DEFAULTS = "edit_role_defaults"
    GRANT_ROLES_OR_OVERRIDES = "grant_roles_or_overrides"
    REVEAL_OTHERS_API_KEYS = "reveal_others_api_keys"
    CHANGE_CONNECTIONS_OR_DEPLOYMENT = "change_connections_or_deployment"
    DOWNLOAD_BACKUPS = "download_backups"
    MANAGE_ENFORCED_FILTER_POLICY = "manage_enforced_filter_policy"


class AccountClass(StrEnum):
    """A target account's class for target-account protection."""

    ORDINARY = "ordinary"
    ADMIN = "admin"
    OWNER = "owner"
