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


class ReviewFilterCommunity(BaseModel):
    """Read-only provenance of a review filter that came from a community snapshot.

    The filter itself is a normal review filter; whether the user edited it is its
    `community_edited`. `label` names the community version, `removed` means the master
    no longer served it at the last refresh ("Removed from community"), and
    `locally_verified` that the filter's own bounds overlap a finding of the same
    category from a completed local model scan of the review's version.
    """

    source_id: str
    filter_id: str
    label: str
    master_revision: int
    confidence: float | None = None
    contributor_count: int | None = None
    low_confidence: bool = False
    removed: bool = False
    locally_verified: bool = False


class ReviewCommunitySource(BaseModel):
    """A stored community filter snapshot and how it compares with its link now.

    `state` is `current` (the link's active filters still equal the snapshot),
    `changed` (they differ; a refresh would bring the changes in), `alignment_required`
    (the link awaits alignment, so it can't be refreshed) or `link_unavailable` (the
    link was deleted, replaced or belongs to another account). The snapshot's filters
    are normal review filters, whatever the state; `filter_count` counts the ones the
    master served when it was taken.
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
    filter_count: int = 0
