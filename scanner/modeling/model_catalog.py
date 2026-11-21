"""Catalog, integration, deployment, and filter-setting API models."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictInt,
    field_validator,
    model_validator,
)

from .media_identity import MediaIdentity
from .model_core import (
    MCF_CATEGORY_TO_FAMILY,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    ReviewFilterId,
    ReviewRevision,
    ReviewWriteModel,
    Severity,
)
from .model_scans import ExportStatus


class ScanModelCapability(BaseModel):
    id: str
    display_name: str
    # Clients label the recommended choice in each list.
    recommended: bool = False


class ModelDownloadState(BaseModel):
    """The acquisition state for one capability, without local path details."""

    state: Literal["not_required", "not_downloaded", "downloading", "ready", "failed", "cancelled"]
    bytes_downloaded: int | None = None
    bytes_total: int | None = None
    error_code: str | None = None
    error_detail: str | None = None


class ModelCapability(ScanModelCapability):
    """A platform-specific, side-effect-free model capability declaration."""

    stage: Literal["audio", "detector", "enrichment"]
    availability: Literal["available", "unavailable"]
    reason: (
        Literal[
            "unsupported_platform",
            "unsupported_architecture",
            "missing_package",
            "missing_provider",
            "deferred_gap",
            "unsupported_dependency_version",
        ]
        | None
    ) = None
    device: Literal["cpu", "cuda", "coreml", "metal", "mlx", "none"]
    weight_requirement: Literal["none", "on_demand"]
    download: ModelDownloadState
    enrichment_behavior: Literal["none", "qwen_summary", "detector_findings_only"] = "none"


class ScanCapabilities(BaseModel):
    """Legacy selectable lists plus detailed native model capability records."""

    audio_models: list[ScanModelCapability]
    audio_filters: list[ScanModelCapability]
    video_models: list[ScanModelCapability]
    qwen_models: list[ScanModelCapability]
    target_id: str
    model_capabilities: list[ModelCapability]


class ManagedModelArtifact(BaseModel):
    """Authenticated view of one manifest-controlled, regenerable artifact."""

    artifact_id: str
    backend_ids: list[str]
    state: ModelDownloadState
    device: Literal["cpu", "coreml"] | None = None
    expected_size: int | None = None
    source_url: str
    license_spdx: str | None = None
    license_status: Literal["declared", "undeclared", "license-review-required"]
    removable: bool


class ModelStorageStatus(BaseModel):
    """Model-management response.  It intentionally contains no cache filesystem path."""

    artifacts: list[ManagedModelArtifact]
    model_capabilities: list[ModelCapability]


class ModelPrefetchRequest(BaseModel):
    backend_id: str


class ModelActionResult(BaseModel):
    """`status` is the inventory, null for a caller who may download but not view it."""

    status: ModelStorageStatus | None
    accepted: bool = False
    removed: bool = False


class JellyfinSettingsUpdate(BaseModel):
    """Write-only connection fields for Editarr's Jellyfin integration."""

    server_url: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False

    @model_validator(mode="after")
    def validate_update(self):
        if self.api_key and self.clear_api_key:
            raise ValueError("Provide an API key or clear it, not both")
        return self


class JellyfinSettings(BaseModel):
    """Safe settings shape returned by Editarr APIs; it never exposes a key."""

    server_url: str | None = None
    api_key_configured: bool = False
    last_checked_at: datetime | None = None
    last_error: str | None = None


class DeploymentUpdate(BaseModel):
    """One field-selective update to persisted deployment overrides."""

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    port: StrictInt | None = None


class DeploymentSnapshotDto(BaseModel):
    """Safe public representation of an immutable deployment snapshot."""

    base_url: str
    public_origin: str
    prefix: str
    listening_port: int
    base_url_source: Literal["saved", "environment", "local_default"]
    port_source: Literal["cli", "saved", "default"]
    cli_port_override: int | None


class DeploymentSavedOverrides(BaseModel):
    """Only explicit persisted fields, keeping an absent override visible as null."""

    base_url: str | None
    port: int | None


class RestartCapability(BaseModel):
    """The supported in-app restart state shared by Settings and the controller."""

    supported: bool
    mode: Literal["standalone_controller", "unsupported"]
    generation: int
    in_progress: bool
    outcome: Literal["idle", "restarting", "applied", "recovered", "failed"]
    target_generation: int | None = None
    reason: Literal["manual_uvicorn", "development_reload", "controller_unavailable"] | None = None
    detail: str | None = None


class DeploymentConfiguration(BaseModel):
    """Current HTTP generation and separately resolved persisted desired state."""

    active: DeploymentSnapshotDto
    next_start: DeploymentSnapshotDto
    saved: DeploymentSavedOverrides
    restart_required: bool
    restart: RestartCapability
    macos_dock_supported: bool = False


class FilterRuleDisposition(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class FilterSelectionRule(ReviewWriteModel):
    id: ReviewFilterId
    disposition: FilterRuleDisposition
    evidence_source: Literal["audio", "visual"] | None = None
    category: McfCategoryFamily | None = None
    subcategory: McfCategory | None = None
    label_contains: str | None = None
    severity: Severity | None = None
    mcf_channel: McfChannel | None = None

    @field_validator("label_contains")
    @classmethod
    def normalize_label_contains(cls, value):
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def validate_matchers(self):
        """Require a nonempty filter rule with a valid category and subcategory pair."""
        if self.subcategory is not None and self.category is None:
            raise ValueError("A subcategory rule must include its category")
        if (
            self.subcategory is not None
            and MCF_CATEGORY_TO_FAMILY[self.subcategory] != self.category
        ):
            raise ValueError("Subcategory does not belong to category")
        if all(
            value is None
            for value in (
                self.evidence_source,
                self.category,
                self.subcategory,
                self.label_contains,
                self.severity,
                self.mcf_channel,
            )
        ):
            raise ValueError("A selection rule requires at least one match clause")
        return self


class McfSeverityDefault(ReviewWriteModel):
    subcategory: McfCategory
    severity: Severity


class FilterDefaultsSettings(ReviewWriteModel):
    revision: ReviewRevision
    selection_rules: list[FilterSelectionRule]
    severity_defaults: list[McfSeverityDefault]

    @model_validator(mode="after")
    def validate_uniqueness(self):
        rule_ids = [rule.id for rule in self.selection_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Selection rule IDs must be unique")
        subcategories = [item.subcategory for item in self.severity_defaults]
        if len(subcategories) != len(set(subcategories)):
            raise ValueError("Severity default subcategories must be unique")
        if set(subcategories) != set(MCF_CATEGORY_TO_FAMILY):
            raise ValueError("Severity defaults must cover every MCF subcategory")
        return self


class FilterDefaultsUpdate(FilterDefaultsSettings):
    pass


class PathMappingCreate(BaseModel):
    remote_path: str
    local_path: str


class PathMappingUpdate(BaseModel):
    remote_path: str | None = None
    local_path: str | None = None


class PathMapping(PathMappingCreate):
    id: int
    sort_order: int
    created_at: datetime
    updated_at: datetime


class PathMappingReorder(BaseModel):
    mapping_ids: list[int]


class PathResolution(BaseModel):
    remote_path: str
    local_path: str
    mapping_id: int | None = None


class JellyfinLibrary(BaseModel):
    """The normalized library fields Editarr needs to build its navigation."""

    id: str
    name: str
    collection_type: str | None = None
    locations: list[str] = Field(default_factory=list)


class JellyfinMediaItem(BaseModel):
    """A normalized Jellyfin item, retained independently from local scan state."""

    id: str
    library_id: str | None = None
    parent_id: str | None = None
    item_type: str
    name: str
    sort_name: str | None = None
    year: int | None = None
    official_rating: str | None = None
    season_number: int | None = None
    index_number: int | None = None
    overview: str | None = None
    path: str | None = None
    runtime_ticks: int | None = None
    duration_seconds: float | None = None
    resolution: str | None = None
    media_format: str | None = None
    media_provider_id: str | None = None
    media_source_count: int | None = None
    versions: list["JellyfinMediaVersion"] = Field(default_factory=list)
    media_streams: list[dict[str, Any]] = Field(default_factory=list)
    image_tags: dict[str, Any] = Field(default_factory=dict)
    image_url: str | None = None
    exports: "ExportStatus" = Field(default_factory=lambda: ExportStatus())
    media_identity: MediaIdentity | None = None
    _detail_loaded: bool = PrivateAttr(default=False)


class JellyfinMediaVersion(BaseModel):
    id: str | None = None
    name: str | None = None
    scene_name: str | None = None
    release_group: str | None = None
    edition: str | None = None
    path: str | None = None
    file_name: str | None = None
    audio_track_count: int | None = None
    container: str | None = None
    resolution: str | None = None
    media_format: str | None = None
    duration_seconds: float | None = None
    size_bytes: int | None = None
    exports: "ExportStatus" = Field(default_factory=lambda: ExportStatus())
    media_identity: MediaIdentity | None = None


class JellyfinMediaPage(BaseModel):
    items: list[JellyfinMediaItem]
    total_count: int
    rating_values: list[str] = Field(default_factory=list)
    resolution_values: list[str] = Field(default_factory=list)
