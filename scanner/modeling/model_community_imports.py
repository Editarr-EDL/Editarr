"""Safe public attribution for locally retained community evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CommunitySourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission_id: str
    digest: str


class CommunityTargetApproval(BaseModel):
    source: Literal["jellyfin"]
    target_item_id: str
    media_source_id: str | None
    channels: list[Literal["audio", "video"]]


class CommunityScanProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_url: str
    contribution_id: str
    revision_id: str
    digest: str
    manifest_digest: str
    signature_digest: str
    author_id: str
    author_key_id: str
    historical_key_status: Literal["active", "retired"]
    artifact_schema: str
    artifact_type: Literal["scan", "manual", "derived"]
    timeline: Literal["source_file", "derived_target"]
    source_duration_seconds: float
    source_references: list[CommunitySourceReference] = Field(default_factory=list)
    retiming: list[dict] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    provenance: dict
    coverage: list[dict]
    analyzed_audio: dict | None = None
