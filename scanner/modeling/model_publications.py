"""Redacted canonical export status for authenticated discovery."""

from typing import Literal

from pydantic import BaseModel, Field

from .model_core import ExportFormat


class ExportPublication(BaseModel):
    id: int
    publication_id: str
    publication_revision: int = Field(ge=1)
    media_item_id: str
    media_source_id: str
    format: ExportFormat
    scope: Literal["personal", "default"]
    target: Literal["self", "review_owner", "item_default"]
    target_user_id: int | None
    target_export_id: str | None
    actor_id: int | None
    policy_owner_id: int | None
    original_author_id: int | None
    policy_revision: int
    review_id: str | None
    exported_format_revision: int | None
    content_sha256: str | None
    current: bool
    outdated: bool
    regeneration_state: Literal["idle", "queued", "running", "succeeded", "failed"] = "idle"
    regenerating: bool = False
    failed: bool = False
    failure_reason: Literal["write_failed"] | None = None
    retryable: bool = False
    serving: bool = True
    desired_policy_revision: int | None = None


class ExportPublications(BaseModel):
    publications: list[ExportPublication]
