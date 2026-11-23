"""Requests and results for verified reuse of previous scan evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model_scans import ReviewTargetRequest, Scan

Channel = Literal["audio", "video"]
ReuseMethod = Literal["original", "manual", "transcript"]


class ReuseAnchor(BaseModel):
    """One matched playback time, scoped to a continuous interval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_seconds: float = Field(ge=0, le=172800, allow_inf_nan=False)
    target_seconds: float = Field(ge=0, le=172800, allow_inf_nan=False)
    segment: int = Field(default=0, ge=0, le=4095, strict=True)


class VersionReuseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_scan_id: str = Field(min_length=1, max_length=128)
    target: ReviewTargetRequest
    channels: list[Channel] = Field(min_length=1, max_length=2)
    audio_track_index: int | None = Field(default=None, ge=0, le=127, strict=True)
    anchors: list[ReuseAnchor] = Field(default_factory=list, max_length=4096)
    method: ReuseMethod = "manual"

    @model_validator(mode="after")
    def validate_selection(self):
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("Select each channel once")
        if self.method != "manual" and self.anchors:
            raise ValueError("Timing points are supplied only in manual alignment")
        return self


class VersionReuseApproval(VersionReuseRequest):
    token: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]


class VersionReuseTranscriptStatus(BaseModel):
    ready: bool
    verify_existing: bool = False


class VersionReuseCue(BaseModel):
    source_finding_id: int
    channel: Literal["audio", "video", "both"]
    source_start: float
    source_end: float | None
    target_start: float | None
    target_end: float | None


class VersionReusePreview(BaseModel):
    source_scan_id: str
    version_id: str
    token: str | None
    target_generation: str | None = None
    verified_channels: list[Channel]
    exact: bool
    issues: list[str]
    cues: list[VersionReuseCue]
    anchors: list[ReuseAnchor]
    alignment_audio_scan_id: str | None = None


class VersionReuseResult(BaseModel):
    scan: Scan
    reused: bool
