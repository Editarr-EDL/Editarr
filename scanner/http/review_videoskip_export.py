"""Prepare download-only VideoSkip files from the current saved review."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from scanner.formats.videoskip_export import export_videoskip, videoskip_milliseconds
from scanner.models import ReviewExportRequest
from scanner.persistence.repository.review_access import not_found
from scanner.persistence.repository.review_export_content import prepare_review_export_context
from scanner.persistence.repository.review_policy_evidence import policy_export_filters

from .review_target_api import _review_http_error


class VideoSkipExportRequest(ReviewExportRequest):
    reference_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    reference_description: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("reference_description", mode="before")
    @classmethod
    def strip_reference_description(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        if (self.reference_seconds is None) != (self.reference_description is None):
            raise ValueError("Provide both the reference timestamp and its description")
        return self


class VideoSkipCompatibilityIssue(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    filter_id: str | None
    message: str
    omitted: bool


class VideoSkipPreflight(BaseModel):
    eligible: bool
    filter_count: int
    exported_count: int
    issue_count: int
    issues: list[VideoSkipCompatibilityIssue]


def prepare_videoskip_download(
    db: Session, review_id: str, request: VideoSkipExportRequest
) -> tuple[VideoSkipPreflight, bytes, str]:
    """Render effective selected cues and describe their VideoSkip representations."""
    context = prepare_review_export_context(
        db,
        review_id,
        request.revision,
        policy_revision=request.policy_revision,
        policy_owner_id=request.policy_owner_id,
    )
    if context is None:
        raise not_found()
    duration = context.duration_seconds
    reference = request.reference_seconds
    if reference is not None and duration is not None:
        milliseconds = videoskip_milliseconds(reference)
        written_after_end = milliseconds is not None and milliseconds / 1_000 >= duration
        if reference >= duration or written_after_end:
            raise _review_http_error(
                "invalid_videoskip_reference",
                "The reference timestamp must be inside the media runtime.",
                422,
            )
    effective = policy_export_filters(context.policy, duration_seconds=duration)
    filters = [cue for cue in effective if cue.selected]
    try:
        output = export_videoskip(
            filters,
            reference_seconds=reference,
            reference_description=request.reference_description,
        )
    except ValueError as exc:
        raise _review_http_error("invalid_videoskip_export", str(exc), 422) from exc

    required_ids = context.policy.required_ids
    required_omitted = any(
        issue.omitted and issue.filter_id in required_ids for issue in output.issues
    )
    prioritized = sorted(
        output.issues, key=lambda issue: (not issue.omitted, issue.filter_id not in required_ids)
    )
    issues = [VideoSkipCompatibilityIssue.model_validate(issue) for issue in prioritized[:100]]
    preflight = VideoSkipPreflight(
        eligible=output.exported_count > 0 and not required_omitted,
        filter_count=len(filters),
        exported_count=output.exported_count,
        issue_count=len(output.issues),
        issues=issues,
    )
    stem = context.media.stem.replace("\\", "_")
    filename = f"{' '.join(stem.split()) or 'review'}.skp"
    return preflight, output.content, filename
