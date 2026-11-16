"""Request and response models for the local authentication endpoints."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, model_validator

from scanner.security.access.api_shapes import CapabilityPayload


class _ReturnTargetModel(BaseModel):
    """Accept only application-relative return targets; responses never redirect directly."""

    return_to: str | None = None

    @model_validator(mode="after")
    def validate_return_to(self):
        if self.return_to is not None and not is_safe_return_to(self.return_to):
            raise ValueError("Return target must be an internal application path")
        return self


class SetupRequest(_ReturnTargetModel):
    """First-run setup: a Jellyfin server and one of its administrators' sign-in."""

    server_url: str = Field(max_length=2048)
    username: str = Field(max_length=256)
    password: SecretStr = Field(max_length=4096)
    remember_me: bool = False


class LoginRequest(_ReturnTargetModel):
    """A Jellyfin user's name and password; the name only selects whom Jellyfin checks."""

    username: str
    password: SecretStr
    remember_me: bool = False


class QuickConnectSignInRequest(_ReturnTargetModel):
    remember_me: bool = False


class QuickConnectAvailability(BaseModel):
    enabled: bool


class QuickConnectAttemptResponse(BaseModel):
    state: Literal["pending", "approved", "expired", "cancelled", "consumed"]
    code: str


class SignInOptions(BaseModel):
    """Sign-in entry points shown to a visitor.

    `jellyfin` is true while a Jellyfin connection is configured.
    """

    jellyfin: bool


class AuthenticationStatusResponse(BaseModel):
    """Setup state and, when signed in, the session's own account and capabilities.

    `display_name` is its profile name, else its username. `jellyfin_user_id` is
    the Jellyfin user the account is linked to. `access` lists what it may
    attempt and its permission revision.
    """

    state: Literal["setup_required", "unauthenticated", "authenticated"]
    setup_required: bool
    sign_in: SignInOptions
    display_name: str | None = None
    jellyfin_user_id: str | None = None
    access: CapabilityPayload | None = None
    csrf_token: str | None = None
    return_to: str | None = None
    restart_required: bool = False


def is_safe_return_to(value: str) -> bool:
    """Return whether ``value`` is a same-origin, application-relative deep link."""

    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == ""
        and parsed.netloc == ""
        and parsed.path.startswith("/")
        and not value.startswith("//")
    )
