"""API response and error shapes for capability-aware clients.

Errors keep the existing access-policy envelope `{"code": ..., "detail": ...}`.
Reauthentication failures use 403, not 401, so a client never treats them as
a signed-out session.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .capability_ids import Capability
from .identity import AccountStatus, CredentialKind, ProviderLinkState
from .vocabulary import Role


class AccessErrorCode(StrEnum):
    AUTHENTICATION_REQUIRED = "authentication_required"
    INVALID_API_KEY = "invalid_api_key"
    ACCOUNT_DISABLED = "account_disabled"
    CAPABILITY_DENIED = "capability_denied"
    PROTECTED_ACCOUNT = "protected_account"
    INTERACTIVE_SESSION_REQUIRED = "interactive_session_required"
    REAUTHENTICATION_REQUIRED = "reauthentication_required"
    REAUTHENTICATION_FAILED = "reauthentication_failed"
    REAUTHENTICATION_THROTTLED = "reauthentication_throttled"
    JELLYFIN_UNAVAILABLE = "jellyfin_unavailable"
    MEDIA_UNAVAILABLE = "media_unavailable"
    NOT_FOUND = "not_found"
    PERMISSION_REVISION_STALE = "permission_revision_stale"


ACCESS_ERROR_STATUS: Mapping[AccessErrorCode, int] = MappingProxyType(
    {
        AccessErrorCode.AUTHENTICATION_REQUIRED: 401,
        AccessErrorCode.INVALID_API_KEY: 401,
        AccessErrorCode.ACCOUNT_DISABLED: 403,
        AccessErrorCode.CAPABILITY_DENIED: 403,
        AccessErrorCode.PROTECTED_ACCOUNT: 403,
        AccessErrorCode.INTERACTIVE_SESSION_REQUIRED: 403,
        AccessErrorCode.REAUTHENTICATION_REQUIRED: 403,
        AccessErrorCode.REAUTHENTICATION_FAILED: 403,
        AccessErrorCode.REAUTHENTICATION_THROTTLED: 429,
        AccessErrorCode.JELLYFIN_UNAVAILABLE: 503,
        AccessErrorCode.MEDIA_UNAVAILABLE: 403,
        AccessErrorCode.NOT_FOUND: 404,
        AccessErrorCode.PERMISSION_REVISION_STALE: 409,
    }
)


class AccessError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: AccessErrorCode
    detail: str


class Reauthentication(BaseModel):
    """Proof sent inside a fresh-interactive request and verified in that request.

    The password is the account's Jellyfin password, checked by Jellyfin. It is
    never logged or audited.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    password: str


class PrincipalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: int
    role: Role
    credential: CredentialKind
    status: AccountStatus
    link_state: ProviderLinkState


class CapabilityPayload(BaseModel):
    """What the signed-in principal may attempt, before per-target narrowing.

    Clients hide controls from this list and refetch when `permission_revision`
    changes. The server still checks every request.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["editarr-capabilities-9"]
    permission_revision: int
    principal: PrincipalSummary
    capabilities: list[Capability]
