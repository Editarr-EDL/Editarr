"""Saved-review deletion impact and per-file disposition requests."""

from typing import Literal

from pydantic import BaseModel, Field

from .model_core import ExportFormat
from .model_publications import ExportPublication

Choice = Literal["retain", "remove"]


class ReviewDeletionFile(BaseModel):
    publication: ExportPublication
    choices: list[Choice]


class ReviewDeletionPreview(BaseModel):
    """Only files the caller may see; hidden publications are retained unannounced."""

    review_id: str
    revision: int
    confirmation: str
    files: list[ReviewDeletionFile]
    legacy_formats: list[ExportFormat]
    scan_ids: list[str]


class ReviewDeletionChoice(BaseModel):
    export_id: int
    choice: Choice


class ReviewDeletionRequest(BaseModel):
    revision: int = Field(ge=0)
    confirmation: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: list[ReviewDeletionChoice] = Field(default_factory=list)


class ReviewDeletionResult(BaseModel):
    review_id: str
    retained: list[int]
    removed: list[int]
