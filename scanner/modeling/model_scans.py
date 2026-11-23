"""Scan, credits, export, and review-summary API models."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from .model_audio_tracks import AudioSelection
from .model_community_imports import CommunityScanProvenance, CommunityTargetApproval
from .model_core import (
    MAX_PERSISTED_INTEGER,
    CreditsConfidenceBand,
    CreditsDetectionMethod,
    CreditsEvidenceKind,
    EditAction,
    ExportFormat,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    McfSourceVersion,
    McfWorkType,
    ReviewScanRole,
    ReviewSource,
    ReviewWriteModel,
    ScanMode,
    ScanOrigin,
    Severity,
    SynchronizationEndMode,
    _validate_classification_pair,
    _validate_mcf_comment,
)
from .model_publications import ExportPublication
from .model_review_repairs import ReviewRepairSummary


class CreditsDetectionConfiguration(ReviewWriteModel):
    detector_version: str = Field(min_length=1)
    tail_window_seconds: float = Field(gt=0, allow_inf_nan=False)
    frame_sample_interval_seconds: float = Field(gt=0, allow_inf_nan=False)
    qwen_model: str | None = None


class CreditsEvidence(ReviewWriteModel):
    kind: CreditsEvidenceKind
    timestamp_seconds: float = Field(ge=0, allow_inf_nan=False)
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)
    detail_code: str = Field(min_length=1)
    details: str | None = None


class CreditsCandidate(ReviewWriteModel):
    id: str = Field(min_length=1)
    boundary_seconds: float = Field(ge=0, allow_inf_nan=False)
    range_start_seconds: float = Field(ge=0, allow_inf_nan=False)
    range_end_seconds: float = Field(gt=0, allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    confidence_band: CreditsConfidenceBand
    method: CreditsDetectionMethod
    evidence: list[CreditsEvidence]

    @model_validator(mode="after")
    def validate_range_and_band(self):
        """Require valid candidate bounds and a confidence band matching its score."""
        if self.range_end_seconds <= self.range_start_seconds:
            raise ValueError("Candidate range end must be after its start")
        expected_band = (
            CreditsConfidenceBand.LOW
            if self.confidence < 0.6
            else CreditsConfidenceBand.MEDIUM
            if self.confidence < 0.8
            else CreditsConfidenceBand.HIGH
        )
        if self.confidence_band != expected_band:
            raise ValueError("Candidate confidence band does not match confidence")
        return self


class CreditsDetectionResult(ReviewWriteModel):
    outcome: Literal["candidate", "no_candidate"]
    candidates: list[CreditsCandidate]
    best_candidate_id: str | None

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.outcome == "no_candidate":
            if self.candidates or self.best_candidate_id is not None:
                raise ValueError("No-candidate results cannot contain candidates")
            return self
        if not self.candidates or self.best_candidate_id != self.candidates[0].id:
            raise ValueError("Candidate results must identify the first candidate as best")
        return self


class CreditsDetection(BaseModel):
    configuration: CreditsDetectionConfiguration
    detector_version: str = Field(min_length=1)
    cache_key: str | None = None
    result: CreditsDetectionResult | None = None
    completed_at: AwareDatetime | None = None


class ScanCoverageInterval(BaseModel):
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Coverage interval end must follow start")
        return self


class ScanStageCoverage(BaseModel):
    stage: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    status: Literal["complete", "partial", "failed", "disabled", "unsupported", "unknown"]
    source: ScanOrigin
    release_version: str | None = None
    scanner_behavior_version: str | None = None
    build_revision: str | None = None
    model_id: str | None = None
    model_revision: str | None = None
    model_digest: str | None = None
    quantization: str | None = None
    effective_settings: dict | None = None
    intervals: list[ScanCoverageInterval] | None = None
    sampling_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class ScanVerificationCoverage(BaseModel):
    """Source finding IDs covered by a derived scan, with totals per source channel."""

    source_counts: dict[Literal["audio", "video"], int]
    covered_finding_ids: dict[Literal["audio", "video"], list[int]]


class Scan(BaseModel):
    """A Editarr scan, numbered independently for each Jellyfin video item."""

    id: str
    scan_id: int
    is_own: bool | None = None
    name: str | None = None
    origin: ScanOrigin = ScanOrigin.GENERATED
    import_source_name: str | None = None
    imported_at: AwareDatetime | None = None
    reuse_pending: bool = False
    stable_version_id: str | None = None
    verified_from_scan_id: str | None = None
    verification_coverage: ScanVerificationCoverage | None = None
    scan_mode: ScanMode = ScanMode.CONTENT
    release_version: str | None = None
    scanner_behavior_version: str | None = None
    build_revision: str | None = None
    audio_model_revision: str | None = None
    video_model_revision: str | None = None
    stage_coverage: list["ScanStageCoverage"] = Field(default_factory=list)
    media_item_id: str
    jellyfin_item_id: str
    library_id: str | None = None
    item_name: str
    item_type: str
    media_source_id: str | None = None
    media_source_name: str | None = None
    status: str
    scan_audio: bool
    audio_model: str | None = None
    audio_filter: str | None = None
    requested_audio_selection: AudioSelection | None = None
    analyzed_audio_selection: AudioSelection | None = None
    scan_video: bool
    video_model: str | None = None
    coarse_scan_interval: float | None = None
    coarse_scan_enabled: bool = False
    qwen_model: str = "qwen3-vl-8b-instruct-4bit"
    qwen_summaries_per_segment: int = 4
    qwen_segment_join_gap_seconds: float = 3.0
    duration_seconds: float | None = None
    video_hash: str | None = None
    checkpoint_stage: str = "queued"
    error_message: str | None = None
    finding_count: int = 0
    manual: bool = False
    edited: bool = False
    scan_started_at: datetime | None = None
    scan_duration_seconds: float | None = None
    scan_duration_as_of: datetime | None = Field(
        default=None, description="Server timestamp of the duration sample while a scan is running."
    )
    created_at: datetime
    updated_at: datetime
    mcf_document: "ScanMcfDocument | None" = None
    credits_detection: CreditsDetection | None = None
    community_provenance: CommunityScanProvenance | None = None
    community_approvals: list[CommunityTargetApproval] = Field(default_factory=list)
    community_pending_targets: list[CommunityTargetApproval] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_credits_detection(self):
        if (self.scan_mode == ScanMode.CREDITS_DETECTION) != (self.credits_detection is not None):
            raise ValueError("Credits detection data must match the scan mode")
        return self


class ScanQueueItem(Scan):
    queue_id: int
    sort_order: int
    error_message: str | None = None
    progress_percent: float | None = None
    progress_stage: str | None = None
    progress_message: str | None = None


class ScanMatchSearch(BaseModel):
    """A catalog item, and optionally one of its versions, to find scanned copies of.

    The server reads the item again; `media_source_id` or `source_path` only
    selects one of the versions it reports. The other fields are ignored.
    """

    source: Literal["jellyfin"]
    item_id: str
    media_source_id: str | None = None
    library_id: str | None = None
    source_path: str | None = None
    item_name: str | None = None
    item_type: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    scene_name: str | None = None
    release_group: str | None = None
    media_format: str | None = None
    media_provider_id: str | None = None


class ScanMatch(BaseModel):
    scan: Scan
    match_reason: Literal["scene_release", "quick_hash", "file_name", "format"]
    already_associated: bool = False


class FilterCandidateSearchResult(BaseModel):
    media_item_id: str
    matches: list[ScanMatch]


class ScanAssociationUpdate(BaseModel):
    media_item_id: str
    scan_ids: list[str] = Field(min_length=1)


class ScanQueueReorder(BaseModel):
    queue_ids: list[int]


class ScanFinding(BaseModel):
    id: int
    scan_id: str
    category: McfCategoryFamily | None
    subcategory: McfCategory | None
    severity: Severity | None = None
    mcf_channel: McfChannel | None = None
    mcf_comment: str | None = None
    start_seconds: float
    end_seconds: float | None = None
    confidence: float | None
    label: str | None = None
    details: str | None = None
    group_key: str | None = None
    source: str
    selected: bool
    edit_action: EditAction

    @field_validator("mcf_comment")
    @classmethod
    def validate_mcf_comment(cls, value):
        return _validate_mcf_comment(value)

    @model_validator(mode="after")
    def validate_classification(self):
        _validate_classification_pair(self.category, self.subcategory, required=False)
        return self


class ScanFindings(BaseModel):
    revision: int
    findings: list[ScanFinding]


class ExportScanReference(BaseModel):
    id: str
    scan_id: int
    role: ReviewScanRole
    name: str | None = None
    origin: ScanOrigin = ScanOrigin.GENERATED
    community_provenance: CommunityScanProvenance | None = None


class ExportSummary(BaseModel):
    format: ExportFormat
    present: bool = False
    exported_at: AwareDatetime | None = None
    review_id: str | None = None
    exported_format_revision: int | None = Field(default=None, ge=0)
    current: bool = False
    outdated: bool = False
    exported_scans: list[ExportScanReference] = Field(default_factory=list)
    exported_review: "SavedReviewSummary | None" = None


def _empty_export_status() -> dict[ExportFormat, ExportSummary]:
    return {export_format: ExportSummary(format=export_format) for export_format in ExportFormat}


class ExportStatus(BaseModel):
    exports: dict[ExportFormat, ExportSummary] = Field(default_factory=_empty_export_status)
    legacy_exports: dict[ExportFormat, ExportSummary] = Field(default_factory=_empty_export_status)
    publications: list[ExportPublication] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_formats(self):
        if set(self.exports) != set(ExportFormat):
            raise ValueError("Export status must include EDL and MCF")
        if any(key != summary.format for key, summary in self.exports.items()):
            raise ValueError("Export status keys must match their summaries")
        return self


class ReviewTarget(BaseModel):
    source: ReviewSource
    item_id: str
    media_source_id: str | None

    @field_validator("media_source_id")
    @classmethod
    def normalize_version(cls, value):
        return value or None


class ReviewTargetRequest(ReviewWriteModel):
    """A Jellyfin item version that an import or credits detection writes to."""

    source: Literal["jellyfin"]
    item_id: str = Field(min_length=1)
    media_source_id: str | None = None


class McfMetadata(BaseModel):
    title: str | None = None
    year: int | None = Field(default=None, ge=0, le=MAX_PERSISTED_INTEGER, strict=True)
    work_type: McfWorkType | None = None
    season: int | None = Field(default=None, gt=0, le=MAX_PERSISTED_INTEGER, strict=True)
    episode: int | None = Field(default=None, gt=0, le=MAX_PERSISTED_INTEGER, strict=True)
    source_url: str | None = None
    imdb_url: str | None = None
    release: str | None = None
    comment: str | None = None

    @field_validator("title", "source_url", "imdb_url", "release", "comment")
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > 4_096 or "\n" in normalized or "\r" in normalized:
            raise ValueError("MCF metadata text must be non-empty and single-line")
        return normalized

    @field_validator("source_url", "imdb_url")
    @classmethod
    def validate_url(cls, value, info):
        """Require an absolute HTTP URL and an IMDb host for IMDb metadata."""
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCF metadata URL must be an absolute HTTP(S) URL")
        if info.field_name == "imdb_url" and not (
            parsed.hostname == "imdb.com" or (parsed.hostname or "").endswith(".imdb.com")
        ):
            raise ValueError("MCF IMDb URL must use an IMDb host")
        return value


class ScanMcfDocument(McfMetadata):
    source_version: McfSourceVersion
    material_start_seconds: float = Field(ge=0, allow_inf_nan=False)
    material_end_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.material_end_seconds <= self.material_start_seconds:
            raise ValueError("Material end must be after material start")
        return self


class ReviewMcfDocument(McfMetadata):
    material_start_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    material_end_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_bounds(self):
        if (
            self.material_start_seconds is not None
            and self.material_end_seconds is not None
            and self.material_end_seconds <= self.material_start_seconds
        ):
            raise ValueError("Material end must be after material start")
        return self


class ReviewSynchronization(ReviewWriteModel):
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)
    end_mode: SynchronizationEndMode

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Synchronization end must be after start")
        return self


class AcceptedCreditsReference(ReviewWriteModel):
    scan_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    accepted_boundary_seconds: float = Field(ge=0, allow_inf_nan=False)


class AcceptedCreditsProvenance(AcceptedCreditsReference):
    source_finding_id: int = Field(gt=0, le=MAX_PERSISTED_INTEGER, strict=True)
    detected_boundary_seconds: float = Field(ge=0, allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    method: CreditsDetectionMethod
    accepted_at: AwareDatetime


class SavedReviewSummary(BaseModel):
    id: str
    media_item_id: str
    target: ReviewTarget
    name: str
    revision: int = Field(ge=0)
    content_revision: int = Field(ge=0)
    edl_content_revision: int = Field(default=0, ge=0)
    mcf_content_revision: int = Field(default=0, ge=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    exports: ExportStatus = Field(default_factory=ExportStatus)
    repair: ReviewRepairSummary | None = None
