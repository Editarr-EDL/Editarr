"""Authorization outcomes and the exception that carries a denial across layers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from starlette.exceptions import HTTPException

from .api_shapes import ACCESS_ERROR_STATUS, AccessErrorCode
from .capability_ids import Capability
from .identity import AccountStatus

DENIAL_DETAILS: Mapping[AccessErrorCode, str] = MappingProxyType(
    {
        AccessErrorCode.AUTHENTICATION_REQUIRED: "Authentication is required",
        AccessErrorCode.INVALID_API_KEY: "API key was rejected",
        AccessErrorCode.ACCOUNT_DISABLED: "This account is disabled",
        AccessErrorCode.CAPABILITY_DENIED: "This account cannot perform this action",
        AccessErrorCode.PROTECTED_ACCOUNT: "This account cannot act on that account",
        AccessErrorCode.INTERACTIVE_SESSION_REQUIRED: "Sign in with a browser session to do this",
        AccessErrorCode.REAUTHENTICATION_REQUIRED: "Confirm your password to do this",
        AccessErrorCode.REAUTHENTICATION_FAILED: "Password confirmation failed",
        AccessErrorCode.REAUTHENTICATION_THROTTLED: "Too many password confirmations; try later",
        AccessErrorCode.JELLYFIN_UNAVAILABLE: "Jellyfin did not answer, so it could not check "
        "the password; nothing changed",
        AccessErrorCode.MEDIA_UNAVAILABLE: "This media is not available to this account",
        AccessErrorCode.NOT_FOUND: "Not found",
        AccessErrorCode.PERMISSION_REVISION_STALE: "Permissions changed; reload and try again",
    }
)


def admission_error(status: AccountStatus) -> AccessErrorCode | None:
    """Return what stops this account from authenticating at all, or ``None``."""

    return AccessErrorCode.ACCOUNT_DISABLED if status is AccountStatus.DISABLED else None


@dataclass(frozen=True)
class Allowed:
    capability: Capability

    @property
    def allowed(self) -> bool:
        return True


@dataclass(frozen=True)
class Denied:
    code: AccessErrorCode

    @property
    def allowed(self) -> bool:
        return False


Decision = Allowed | Denied


class AccessDenied(HTTPException):
    """A denial raised by a handler, service, or worker check.

    It is an HTTP exception so an app without the access error handler still
    answers with the right status; `install_access_errors` adds the envelope.
    """

    def __init__(self, code: AccessErrorCode, *, retry_after: int | None = None) -> None:
        super().__init__(
            status_code=ACCESS_ERROR_STATUS[code],
            detail=DENIAL_DETAILS[code],
            headers={"Cache-Control": "no-store"}
            | ({"Retry-After": str(retry_after)} if retry_after is not None else {}),
        )
        self.code = code
