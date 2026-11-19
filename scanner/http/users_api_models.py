"""Request and response bodies for the Users, permissions, and media-access routes.

Profile values arrive as plain strings and are validated in the
handlers, so a bad value answers in the `{"code", "detail"}` envelope.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scanner.security.access.identity import AccountStatus, DisableCause
from scanner.security.access.vocabulary import Role
from scanner.security.account_links.cleanup import LinkCleanup
from scanner.security.account_ownership import SuccessorRefusal
from scanner.security.account_profiles import SUPPORTED_LANGUAGES
from scanner.security.account_repository import (
    JELLYFIN_PROFILE_FIELDS,
    AccountRecord,
    AccountSource,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OverrideIndicators(BaseModel):
    permissions: bool
    media_access: bool
    filter_policy: bool


class AccountSummary(BaseModel):
    """The approved Users-list columns. Preferences appear only in a detail read."""

    id: int
    username: str | None
    display_name: str | None
    source: AccountSource
    role: Role
    status: AccountStatus
    disable_cause: DisableCause | None = Field(
        description="Why a disabled account is disabled; `unlinked` means it has no Jellyfin link"
    )
    last_login_at: datetime | None
    created_at: datetime
    avatar: str | None = Field(description="Avatar digest for cache keys, or null")
    overrides: OverrideIndicators

    @classmethod
    def of(cls, record: AccountRecord) -> AccountSummary:
        return cls(**_summary(record))


class AccountPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[AccountSummary]


class AccountDetail(AccountSummary):
    language: str | None
    read_only_fields: list[Literal["username", "display_name", "avatar"]] = Field(
        description="Fields synced from Jellyfin for a linked account"
    )
    available_languages: list[str] = Field(
        description="Display languages with complete interface text"
    )
    link_cleanup: LinkCleanup | None = Field(
        description=(
            "Old export removal after the latest link, or null when the account has no "
            "completed or removing link transition, including imported and rolled-back accounts"
        )
    )
    successor_refusal: SuccessorRefusal | None = Field(
        description=(
            "Why ownership transfer to this account would be refused now; null when it "
            "would be allowed or the caller cannot transfer ownership to it"
        )
    )

    @classmethod
    def of(
        cls,
        record: AccountRecord,
        link_cleanup: LinkCleanup | None,
        successor_refusal: SuccessorRefusal | None,
    ) -> AccountDetail:
        linked = record.source is AccountSource.JELLYFIN
        return cls(
            **_summary(record),
            language=record.language,
            read_only_fields=list(JELLYFIN_PROFILE_FIELDS) if linked else [],
            available_languages=list(SUPPORTED_LANGUAGES),
            link_cleanup=link_cleanup,
            successor_refusal=successor_refusal,
        )


def _summary(record: AccountRecord) -> dict:
    return {
        "id": record.user_id,
        "username": record.username,
        "display_name": record.display_name,
        "source": record.source,
        "role": record.role,
        "status": record.status,
        "disable_cause": record.disable_cause,
        "last_login_at": record.last_login_at,
        "created_at": record.created_at,
        "avatar": record.avatar_digest,
        "overrides": OverrideIndicators(
            permissions=record.permission_overrides,
            media_access=record.media_access_override,
            filter_policy=record.filter_policy_override,
        ),
    }


class ProfileFields(_Strict):
    """Optional profile values. In a change, a listed `null` clears the field."""

    display_name: str | None = None
    language: str | None = None


class StatusChange(_Strict):
    status: AccountStatus


class SessionsRevoked(BaseModel):
    sessions_revoked: int
