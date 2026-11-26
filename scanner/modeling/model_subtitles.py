"""Review subtitle discovery, preview and publication wire models."""

from typing import Literal

from pydantic import BaseModel, Field

from .model_reviews import ReviewExportRequest


class SubtitleSourcesRequest(ReviewExportRequest):
    audio_source_id: str | None = Field(default=None, min_length=1, max_length=128)


class SubtitleSourceRequest(SubtitleSourcesRequest):
    source_id: str = Field(min_length=1, max_length=64)


class SubtitlePublishRequest(SubtitleSourceRequest):
    preview_token: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewAudioSource(BaseModel):
    id: str
    label: str
    language: str | None = None


class SubtitleSource(BaseModel):
    id: str
    label: str
    language: str | None
    format: str
    kind: Literal["external", "embedded", "transcript"]
    forced: bool = False
    sdh: bool = False
    supported: bool = True
    recommended: bool = False
    published_filename: str | None = None


class PublishedSubtitle(BaseModel):
    filename: str
    label: str
    format: str
    forced: bool
    owned_by_viewer: bool = False


class SubtitleSources(BaseModel):
    sources: list[SubtitleSource]
    fallback: Literal["available", "scan_required", "choose_audio"] | None
    audio_sources: list[ReviewAudioSource]
    audio_source_id: str | None
    published_subtitles: list[PublishedSubtitle]
    track_title: str | None = None
    publication_block_reason: str | None = None


class SubtitlePreviewCue(BaseModel):
    start_seconds: float
    end_seconds: float
    original: str
    filtered: str


class SubtitlePreview(BaseModel):
    preview_token: str | None = None
    source: SubtitleSource
    cues: list[SubtitlePreviewCue]
    changed_cues: int
    unchanged_cues: int
    track_title: str | None = None
    filename: str | None = None
    publication_block_reason: str | None = None


class SubtitlePublication(BaseModel):
    source: SubtitleSource
    filename: str
    replaced: bool
    refresh_pending: bool = False
