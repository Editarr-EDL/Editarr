"""Ownership, authorship, export provenance, media decisions, and audit-event contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import NewType

from scanner.formats.export_layout import ExportId
from scanner.models import ExportFormat

from .capability_ids import Capability
from .identity import CredentialKind, UserId

PolicyRevision = NewType("PolicyRevision", int)

AUDIT_RETENTION = timedelta(days=90)


@dataclass(frozen=True)
class ReviewOwnership:
    """The current owner, who is also the policy owner, and the immutable original author.

    Copy and transfer change `owner`; they never change `original_author`.
    """

    owner: UserId
    original_author: UserId

    @property
    def policy_owner(self) -> UserId:
        return self.owner


@dataclass(frozen=True)
class PersonalTarget:
    """A personal export for one account. The export ID fixes the destination path."""

    user_id: UserId
    export_id: ExportId


@dataclass(frozen=True)
class ItemDefaultTarget:
    """The item default; its existence is the plugin fallback switch."""


ExportTarget = PersonalTarget | ItemDefaultTarget


@dataclass(frozen=True)
class ExportProvenance:
    """Who exported what, for whom, and under whose policy.

    `policy_owner` is the review owner at export time, regardless of target,
    and both targets apply that owner's enforced policy. The regeneration
    guarantee covers personal targets only.
    """

    export_format: ExportFormat
    target: ExportTarget
    actor: UserId
    source_review_id: str
    review_revision: int
    policy_owner: UserId
    original_author: UserId
    policy_revision: PolicyRevision


class MediaBasis(StrEnum):
    OWNER = "owner"
    LOCAL_DEFAULT = "local_default"
    LOCAL_OVERRIDE = "local_override"
    JELLYFIN_VERIFIED = "jellyfin_verified"
    JELLYFIN_GRACE = "jellyfin_grace"


class MediaDenial(StrEnum):
    NOT_IN_SCOPE = "not_in_scope"
    PARENTAL = "parental"
    UNMATCHED = "unmatched"
    """Outside every Jellyfin library folder and matching no Jellyfin item; an owner
    exception can allow it."""
    LIBRARY_UNMATCHED = "library_unmatched"
    """Inside a Jellyfin library folder yet matching no Jellyfin item; no exception applies."""
    UNRESOLVED = "unresolved"
    """Matching, library-folder, or Jellyfin evidence could not be read or parsed; no
    exception applies."""
    VERIFICATION_EXPIRED = "verification_expired"
    SERVER_REPLACED = "server_replaced"


@dataclass(frozen=True)
class MediaAllowed:
    basis: MediaBasis


@dataclass(frozen=True)
class MediaDenied:
    """Callers answer `not_found` unless the target's existence is already visible."""

    reason: MediaDenial


MediaDecision = MediaAllowed | MediaDenied


class AuditOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"


_DETAIL_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
_SECRET_WORDS = ("password", "secret", "token", "api_key", "apikey", "private", "credential")


@dataclass(frozen=True)
class AuditEvent:
    """One secret-free audit row, retained for `AUDIT_RETENTION`.

    `actor` is None only for system work such as scheduled sync. Admin queries
    are scoped by `target_user` and `media_key`. `details` is a read-only
    snapshot of string, integer, boolean, or null values; `dict(details)` is
    its JSON form.
    """

    occurred_at: datetime
    actor: UserId | None
    credential: CredentialKind | None
    action: Capability | str
    outcome: AuditOutcome
    target_user: UserId | None = None
    media_key: str | None = None
    resource: str | None = None
    details: Mapping[str, str | int | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.occurred_at.utcoffset() is None:
            raise ValueError("Audit time must be timezone-aware")
        details = dict(self.details)
        for key, value in details.items():
            if (
                not isinstance(key, str)
                or not _DETAIL_KEY.fullmatch(key)
                or any(word in key for word in _SECRET_WORDS)
            ):
                raise ValueError(f"Audit detail key {key!r} is not allowed")
            if value is not None and not isinstance(value, str | int):
                raise ValueError(f"Audit detail {key!r} must be a scalar")
        object.__setattr__(self, "details", MappingProxyType(details))
