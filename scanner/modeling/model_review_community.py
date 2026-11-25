"""Saved-review community filter snapshots: membership requests and read models."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .model_core import ReviewWriteModel

_ID = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")

CommunitySourceState = Literal["current", "changed", "alignment_required", "link_unavailable"]


class ReviewCommunityMembership(ReviewWriteModel):
    """One included community filter snapshot in a save request.

    `{source_id}` keeps a stored snapshot, `{source_id, refresh: true}` takes a new
    snapshot from the same link, and `{link_id}` includes a link with a new snapshot.
    Saved snapshots that a request leaves out are removed.
    """

    source_id: str | None = _ID
    link_id: str | None = _ID
    refresh: bool = False

    @model_validator(mode="after")
    def validate_identity(self):
        if (self.source_id is None) == (self.link_id is None):
            raise ValueError("Send exactly one of source_id or link_id")
        if self.refresh and self.source_id is None:
            raise ValueError("refresh applies to a stored source_id")
        return self


class ReviewCommunityFilter(BaseModel):
    """One snapshot filter on the local timeline, in seconds."""

    filter_id: str
    category: str
    subcategory: str
    severity: str
    edit_action: Literal["mute", "skip"]
    start_seconds: float
    end_seconds: float
    confidence: float | None = None


class CommunitySubtitleSummary(BaseModel):
    """How the review's community language mutes reach filtered subtitles.

    `applied` mutes align with a word-level language finding of a local audio scan and
    redact its words; `needs_audio_scan` mutes wait for a completed local audio scan of
    the version; `unaligned` mutes overlap no local language finding. Only applied
    mutes change subtitles.
    """

    applied: int = 0
    needs_audio_scan: int = 0
    unaligned: int = 0


class ReviewCommunitySource(BaseModel):
    """A stored community filter snapshot and how it compares with its link now.

    `state` is `current` (the link's active filters still equal the snapshot),
    `changed` (they differ; a refresh would update the snapshot), `alignment_required`
    (the link awaits alignment, so it can't be refreshed) or `link_unavailable` (the
    link was deleted, replaced or belongs to another account). The review always uses
    `filters`, whatever the state.
    """

    source_id: str
    link_id: str | None
    community_version_id: str
    label: str
    tier: str | None = None
    match_percent: int | None = None
    master_revision: int
    alignment: dict | None = None
    captured_at: str
    state: CommunitySourceState
    link_state: str | None = None
    filters: list[ReviewCommunityFilter] = Field(default_factory=list)
    # Snapshot filters left out of exports because their subcategory doesn't belong to
    # their category.
    unusable_filter_ids: list[str] = Field(default_factory=list)
