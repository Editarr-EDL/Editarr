"""Strict local lookup inputs and bounded public catalog responses."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from scanner.formats.community.artifact_v2 import AnalyzedAudio
from scanner.formats.community.codec import Coverage, WorkId
from scanner.formats.community.contribution_metadata import MAX_SUMMARY_MODELS, MODEL_TOKEN

from .community_matching import Band, MatchEvidence
from .model_audio_tracks import AudioSelection

Count = Annotated[int, Field(ge=0, le=(1 << 53) - 1, strict=True)]


class LookupModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LookupTarget(LookupModel):
    source: Literal["jellyfin"]
    item_id: str = Field(min_length=1, max_length=4096)
    media_source_id: str | None = Field(default=None, max_length=128)


class LookupRequest(LookupModel):
    target: LookupTarget
    audio_selection: AudioSelection | None = None
    page_size: int = Field(default=16, ge=1, le=96, strict=True)
    cursor: str | None = Field(default=None, max_length=2048)
    target_key: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    refresh: StrictBool = False


class ChannelCompatibility(LookupModel):
    band: Band
    reasons: list[str] = Field(max_length=64)
    conflicts: list[str] = Field(max_length=64)
    missing: list[str] = Field(max_length=64)
    strong_eligible: StrictBool


class LookupCompatibility(LookupModel):
    policy_version: str = Field(max_length=128)
    video: ChannelCompatibility
    audio: ChannelCompatibility
    selected: Literal[False]


class LookupAttribution(LookupModel):
    contributorId: str = Field(min_length=1, max_length=128)
    displayName: str = Field(min_length=1, max_length=128)


class LookupStatistics(LookupModel):
    imports: int = Field(ge=0, le=(1 << 53) - 1, strict=True)
    likes: int = Field(ge=0, le=(1 << 53) - 1, strict=True)
    dislikes: int = Field(ge=0, le=(1 << 53) - 1, strict=True)


class LookupRanking(LookupModel):
    policyVersion: Literal["community-ranking-wilson-1"]
    qualityScore: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    voteCount: Count
    uploaderBoost: int = Field(ge=0, le=0, strict=True)
    score: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)


class SummarySourceState(LookupModel):
    source_id: str = Field(max_length=128)
    origin: Literal["generated", "manual", "imported"]
    status: Literal["complete", "failed", "cancelled"]


class SummaryModel(LookupModel):
    capability: str = Field(pattern=MODEL_TOKEN)
    model_id: str = Field(pattern=MODEL_TOKEN)
    model_revision: str | None = Field(default=None, pattern=MODEL_TOKEN)


class SummaryManualFilterCounts(LookupModel):
    added: Count | None
    edited: Count | None


class LookupSummary(LookupModel):
    schema_version: Literal["community-summary-1", "community-summary-2"]
    registry_version: Literal["mcf-1.1.0"]
    submission_id: str = Field(max_length=128)
    kind: Literal["scan", "manual", "derived"]
    work_ids: list[WorkId] = Field(max_length=32)
    release: str | None = Field(max_length=256)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    release_version: str | None = Field(max_length=128)
    scanner_behavior_version: str | None = Field(max_length=128)
    total: Count
    sources: dict[Literal["audio", "visual", "manual", "derived"], Count]
    channels: dict[Literal["audio", "video"], Count]
    taxonomy: dict[str, Count]
    coverage: list[Coverage] = Field(max_length=128)
    name: str | None = Field(default=None, max_length=128)
    matching: MatchEvidence | None = None
    audio_association: Literal["none", "unknown", "identified"] | None = None
    source_revision: int | None = Field(default=None, ge=0)
    analyzed_audio: AnalyzedAudio | None = None
    source_states: list[SummarySourceState] | None = Field(default=None, max_length=128)
    models: list[SummaryModel] | None = Field(default=None, max_length=MAX_SUMMARY_MODELS)
    manual_filter_counts: SummaryManualFilterCounts | None = None


class LookupCandidate(LookupModel):
    contributionId: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    revisionId: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    artifactDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifactSchema: Literal["community-artifact-1", "community-artifact-2"]
    releaseId: str = Field(min_length=1, max_length=128)
    catalogCreatedAt: int = Field(ge=0, le=(1 << 53) - 1, strict=True)
    attribution: LookupAttribution
    summary: LookupSummary
    evidence: MatchEvidence
    compatibility: LookupCompatibility
    statistics: LookupStatistics
    ranking: LookupRanking | None


class LookupPage(LookupModel):
    version: Literal[1]
    policyVersion: str = Field(max_length=128)
    candidates: list[LookupCandidate] = Field(max_length=96)
    nextCursor: str | None = Field(max_length=2048)


class LookupAvailabilityItem(LookupModel):
    id: Literal["target"]
    status: Literal["ok", "missing_identity"]
    candidateCount: int | None = Field(ge=0, le=96, strict=True)
    truncated: StrictBool
    channels: dict[Literal["audio", "video"], dict[Band, int]] | None


class LookupAvailability(LookupModel):
    version: Literal[1]
    policyVersion: str = Field(max_length=128)
    items: list[LookupAvailabilityItem] = Field(min_length=1, max_length=1)
