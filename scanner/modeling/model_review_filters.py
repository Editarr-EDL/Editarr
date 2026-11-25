"""Saved-review filter models: one filter from a scan or a community snapshot."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .model_core import (
    MAX_PERSISTED_INTEGER,
    EditAction,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    ReviewFilterId,
    ReviewWriteModel,
    Severity,
    _validate_classification_pair,
    _validate_mcf_comment,
)
from .model_review_community import ReviewFilterCommunity

# Fields naming where a review filter came from, or how the user treated it, rather
# than what it does; scan findings don't have them.
PROVENANCE_FIELDS = frozenset(
    {
        "id",
        "source_scan_id",
        "source_finding_id",
        "community_source_id",
        "community_filter_id",
        "community_edited",
    }
)


class ReviewFilter(BaseModel):
    """A review filter from a scan (`source_scan_id`) or from an included community
    snapshot (`community_source_id` and the master's `community_filter_id`)."""

    id: str
    source_scan_id: str | None = None
    source_finding_id: int | None = Field(default=None, gt=0, le=MAX_PERSISTED_INTEGER, strict=True)
    community_source_id: str | None = Field(default=None, max_length=36)
    community_filter_id: str | None = Field(default=None, max_length=128)
    # A community filter the user edited directly; bulk defaults leave it unset. Clients
    # set it when the user changes a community filter's enabled state, times,
    # classification, severity or action. It never resets.
    community_edited: bool | None = None
    source: Literal["audio", "visual"]
    category: McfCategoryFamily | None
    subcategory: McfCategory | None
    severity: Severity | None = None
    mcf_channel: McfChannel | None = None
    mcf_comment: str | None = None
    label: str | None
    details: str | None
    group_key: str | None
    start_seconds: float
    end_seconds: float | None
    confidence: float | None
    edit_action: EditAction
    selected: bool

    @field_validator("mcf_comment")
    @classmethod
    def validate_mcf_comment(cls, value):
        return _validate_mcf_comment(value)

    @model_validator(mode="after")
    def validate_classification(self):
        _validate_classification_pair(self.category, self.subcategory, required=False)
        community = self.community_source_id is not None
        if (
            (self.source_scan_id is None) != community
            or (self.community_filter_id is None) == community
            or (community and self.source_finding_id is not None)
        ):
            raise ValueError("A review filter needs either a source scan or a community source")
        if not community:
            self.community_edited = None
        return self


class SavedReviewFilter(ReviewFilter):
    """A saved review's filter with read-only edit provenance."""

    # Accept plain ReviewFilter values (label_edited then defaults to False).
    model_config = ConfigDict(from_attributes=True)

    # True when the stored label differs from the label of the scan finding the filter
    # was copied from, i.e. someone changed it. False when it matches, and when the
    # source finding is unavailable (provenance unknown). A hand-added filter's label is
    # its source finding's, so it is False there too; its scan origin is "manual".
    label_edited: bool = False
    # Set for a filter from a community snapshot.
    community: ReviewFilterCommunity | None = None


class ReviewFilterInput(ReviewFilter, ReviewWriteModel):
    id: ReviewFilterId

    @model_validator(mode="before")
    @classmethod
    def ignore_read_only_provenance(cls, value):
        """Accept and drop SavedReviewFilter's read-only fields when a client echoes them."""
        if isinstance(value, dict) and value.keys() & {"label_edited", "community"}:
            value = {
                key: item for key, item in value.items() if key not in {"label_edited", "community"}
            }
        return value

    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float | None = Field(allow_inf_nan=False)
    confidence: float | None = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
            raise ValueError("End time must be after start time")
        return self


__all__ = ["PROVENANCE_FIELDS", "ReviewFilter", "ReviewFilterInput", "SavedReviewFilter"]
