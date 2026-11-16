"""Request and response shapes for proof-bound account linking (MU-35)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from scanner.security.access.api_shapes import Reauthentication
from scanner.security.access.identity import DisableCause


class JellyfinSignIn(BaseModel):
    """The Jellyfin credentials of the identity to link. Never stored or audited."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(max_length=1024)


class LinkProofRequest(BaseModel):
    """Prove the caller and the Jellyfin identity to link to one account."""

    model_config = ConfigDict(extra="forbid")

    reauthentication: Reauthentication | None = None
    jellyfin: JellyfinSignIn


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reauthentication: Reauthentication | None = None
    proof: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=64, max_length=64)


class IdentityValue(BaseModel):
    server_id: str
    user_id: str


class IdentityChange(BaseModel):
    current: IdentityValue | None
    after: IdentityValue


class ExportIdChange(BaseModel):
    current: str
    after: str


class RoleChange(BaseModel):
    current: Literal["owner", "admin", "user"]
    after: Literal["owner", "admin", "user"]


class MediaChange(BaseModel):
    current: Literal["local", "jellyfin", "suspended"]
    after: Literal["jellyfin"]
    all_libraries: bool
    libraries: int
    parental_limit: bool


class NameChange(BaseModel):
    current: str | None
    after: str


class ProfileChange(BaseModel):
    name: NameChange
    avatar: Literal["jellyfin", "none"]
    synced_read_only: bool


class StatusChange(BaseModel):
    """The account's status before and after linking.

    Linking enables an account disabled for having no link (`cause` `unlinked`);
    any other disable cause stays.
    """

    current: Literal["active", "disabled"]
    after: Literal["active", "disabled"]
    cause: DisableCause | None


class ExportMove(BaseModel):
    export_id: int
    format: Literal["edl", "mcf"]
    media_item_id: str
    media_source_id: str
    publication_revision: int
    action: Literal["move", "record"]
    conflict: str | None


class LinkPreview(BaseModel):
    """What confirming the link changes. `blockers` must be empty to confirm."""

    user_id: int
    kind: Literal["link", "relink"] | None
    identity: IdentityChange
    export_id: ExportIdChange
    role: RoleChange
    media: MediaChange
    profile: ProfileChange
    proof_basis: Literal["actor_authentication", "admin_attested"]
    """`actor_authentication` (the caller's own account) or `admin_attested` (another
    account, on the caller's fresh authentication and the new Jellyfin sign-in)."""
    status: StatusChange
    exports: list[ExportMove]
    blockers: list[str]


class LinkProofResponse(BaseModel):
    proof: str
    expires_at: datetime
    preview: LinkPreview
    confirmation: str


class LinkResult(BaseModel):
    """`removing` means the link committed and some old files await recovery."""

    state: Literal["completed", "removing"]
    preview: LinkPreview
