"""Account, provider identity, credential, and principal contracts.

These types keep apart who is acting (`Principal`), which account is stored
(`Account`), how it signed in (`CredentialKind`), and which provider identity it
is linked to (`ProviderIdentity`). Owners, authors, and export targets are in
`records`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

from scanner.formats.export_layout import ExportId, JellyfinUserId, LocalExportId

from .vocabulary import AccountClass, Role

UserId = NewType("UserId", int)
"""Immutable internal account ID. Never reused; the migrated administrator keeps its ID."""

PermissionRevision = NewType("PermissionRevision", int)
"""Monotonic revision of everything that changes one account's effective permissions."""

JellyfinServerId = NewType("JellyfinServerId", str)


class CredentialKind(StrEnum):
    SESSION = "session"
    API_KEY = "api_key"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    """Disabled by an admin or by a confirmed upstream disable/delete. Data is retained."""


class DisableCause(StrEnum):
    """Why a disabled account is disabled; an active account has none."""

    LOCAL = "local"
    """The owner or an admin disabled it; only they enable it again."""
    UPSTREAM_DISABLED = "upstream_disabled"
    """Jellyfin disabled the user; Jellyfin enabling the user enables the account."""
    UPSTREAM_DELETED = "upstream_deleted"
    """Jellyfin deleted the user; the same identity never returns on its own."""
    UNLINKED = "unlinked"
    """The account has no Jellyfin link (migration 0030); linking it enables it."""


class ProviderLinkState(StrEnum):
    UNLINKED = "unlinked"
    VERIFIED = "verified"
    OUTAGE_GRACE = "outage_grace"
    """Refresh failed within 24 hours of the last success; last-verified access applies."""
    OUTAGE_EXPIRED = "outage_expired"
    """More than 24 hours without verification; profile access only, media blocked."""
    SERVER_REPLACED = "server_replaced"
    """The linked server is no longer configured; media is blocked until relinking."""


@dataclass(frozen=True)
class ProviderIdentity:
    """A Jellyfin identity, unique by server and user ID and never matched by name."""

    server_id: JellyfinServerId
    user_id: JellyfinUserId


@dataclass(frozen=True)
class Account:
    """The persisted account state that authorization reads on every request."""

    user_id: UserId
    role: Role
    status: AccountStatus
    local_export_id: LocalExportId
    provider: ProviderIdentity | None
    link_state: ProviderLinkState

    def __post_init__(self) -> None:
        if (self.provider is None) != (self.link_state is ProviderLinkState.UNLINKED):
            raise ValueError("A provider identity and a linked state must appear together")
        if self.role is Role.OWNER and (self.provider is None) != (
            self.status is AccountStatus.DISABLED
        ):
            raise ValueError("The owner is active exactly while it has a Jellyfin link")

    @property
    def export_id(self) -> ExportId:
        """Linked accounts export under the Jellyfin ID; local-only accounts under the local ID."""

        return self.provider.user_id if self.provider else self.local_export_id

    @property
    def account_class(self) -> AccountClass:
        return {
            Role.USER: AccountClass.ORDINARY,
            Role.ADMIN: AccountClass.ADMIN,
            Role.OWNER: AccountClass.OWNER,
        }[self.role]


@dataclass(frozen=True)
class Principal:
    """The authenticated actor for one request or durable job.

    Jobs store the initiating `user_id` and revision, then re-read the account
    and recheck capabilities when they execute.
    """

    account: Account
    credential: CredentialKind
    permission_revision: PermissionRevision

    @property
    def user_id(self) -> UserId:
        return self.account.user_id
