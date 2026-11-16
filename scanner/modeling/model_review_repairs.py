"""Saved-review orphan recovery response contracts."""

from pydantic import AwareDatetime, BaseModel


class ReviewRecoveryCounts(BaseModel):
    kept_cues: int
    removed_memberships: int
    removed_accepted_credits: int


class ReviewCommunityRepair(BaseModel):
    available: bool
    unavailable_reason: str | None = None


class ReviewRepairSummary(BaseModel):
    message: str = "This review uses a scan that no longer exists."
    missing_scan_ids: list[str]
    detected_at: AwareDatetime
    recover: ReviewRecoveryCounts
    community: ReviewCommunityRepair | None = None
