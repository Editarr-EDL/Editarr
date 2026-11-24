"""Account deletion consent, confirmed requests, and per-resource results (MU-38).

Responses name only reviews, scans, and jobs the actor may see; everything else
is a `hidden` count.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scanner.security.access.api_shapes import Reauthentication

from .model_publications import ExportPublication
from .model_review_deletion import Choice, ReviewDeletionChoice

Mode = Literal["retain", "transfer", "delete"]
JellyfinReturn = Literal["allow", "block"]


class AccountDeletionFile(BaseModel):
    """A published file of one of the account's reviews that the caller may see."""

    review_id: str
    publication: ExportPublication
    choices: list[Choice]


class AccountDeletionReviews(BaseModel):
    count: int
    hidden: int
    ids: list[str]


class AccountDeletionScan(BaseModel):
    scan_id: str
    status: str
    required: bool = Field(
        description="Another review, a kept publication, or pending community work needs it"
    )


class AccountDeletionScans(BaseModel):
    hidden: int
    items: list[AccountDeletionScan]


class AccountDeletionJobs(BaseModel):
    waiting: list[str]
    running: list[str]
    hidden: int


class AccountDeletionPublications(BaseModel):
    """Personal publications of the account's reviews that the caller may see."""

    hidden: int
    items: list[ExportPublication]


class AccountDeletionTransfer(BaseModel):
    files_moved: int = Field(description="Personal files moving to the destination's export ID")
    policy_files: int = Field(
        description="Retained files whose policy owner becomes the destination"
    )


class AccountDeletionCredentials(BaseModel):
    sessions: int
    api_key: bool
    linked: bool


class AccountDeletionCommunity(BaseModel):
    identity: bool
    operations: int
    pending: int
    master_links: int = Field(0, description="Master-set links, including replaced history")
    master_notifications: int = 0
    master_preferences: bool = False
    master_sync_states: int = 0


class AccountDeletionBlocker(BaseModel):
    code: str
    count: int


class AccountDeletionPreview(BaseModel):
    """What the chosen mode does; confirm `confirmation` to apply exactly this."""

    user_id: int
    mode: Mode
    destination_id: int | None
    confirmation: str
    reviews: AccountDeletionReviews
    files: list[AccountDeletionFile]
    scans: AccountDeletionScans
    jobs: AccountDeletionJobs
    personal_files: int
    publications: AccountDeletionPublications
    transfer: AccountDeletionTransfer | None
    credentials: AccountDeletionCredentials
    community: AccountDeletionCommunity
    blockers: list[AccountDeletionBlocker]


class AccountDisposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode
    confirmation: str = Field(pattern=r"^[0-9a-f]{64}$")
    destination_id: int | None = None
    files: list[ReviewDeletionChoice] = Field(default_factory=list)
    jellyfin_user: JellyfinReturn = Field(
        "allow",
        description=(
            "For a linked account: `allow` lets the Jellyfin user's next sign-in create a "
            "fresh account; `block` keeps that Jellyfin user from signing in or being "
            "imported until the owner unblocks them. A local account ignores it."
        ),
    )

    @model_validator(mode="after")
    def consistent(self):
        if (self.mode == "transfer") != (self.destination_id is not None):
            raise ValueError("Name a destination for a transfer and only for a transfer")
        if len({file.export_id for file in self.files}) != len(self.files):
            raise ValueError("List each published file once")
        return self


class AccountDeletionRequest(AccountDisposition):
    reauthentication: Reauthentication | None = None


class AccountDeletionResult(BaseModel):
    """The per-resource result; `replayed` marks a retry of a completed deletion."""

    user_id: int
    mode: Mode
    destination_id: int | None
    replayed: bool
    reviews_deleted: list[str]
    reviews_retained: list[str]
    reviews_transferred: list[str]
    reviews_hidden: int
    files_retained: list[int]
    files_removed: list[int]
    files_moved: int
    scans_deleted: list[str]
    scans_kept: list[str]
    scans_hidden: int
    jobs_stopped: list[str]
    jobs_hidden: int
    personal_files_kept: int
    community_operations_kept: int
    community_identity_kept: bool
    community_master_links_deleted: int = 0
    community_master_links_transferred: int = 0
    sessions_revoked: int
    api_key_revoked: bool
    identity_released: bool
    profile_removed: bool
    jellyfin_blocked: bool = Field(
        False, description="The deleted account's Jellyfin user is now blocked"
    )


class BulkAccountDeletionTarget(AccountDisposition):
    user_id: int


class BulkAccountDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[BulkAccountDeletionTarget] = Field(min_length=1, max_length=100)
    reauthentication: Reauthentication | None = None

    @model_validator(mode="after")
    def unique_targets(self):
        if len({target.user_id for target in self.targets}) != len(self.targets):
            raise ValueError("Each account may appear once")
        return self


class BulkAccountDeletionOutcome(BaseModel):
    user_id: int
    outcome: Literal["applied", "refused"]
    code: str | None = None
    result: AccountDeletionResult | None = None


class BulkAccountDeletionResponse(BaseModel):
    """Every target in request order; earlier applied targets stay applied."""

    results: list[BulkAccountDeletionOutcome]
