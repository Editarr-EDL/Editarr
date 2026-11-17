"""Saved-review editing, import, export, and error API models."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from .model_core import (
    MAX_PERSISTED_INTEGER,
    EditAction,
    ExportFormat,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    McfSourceVersion,
    ReviewFilterId,
    ReviewName,
    ReviewRevision,
    ReviewScanRole,
    ReviewWriteModel,
    Severity,
    SynchronizationEndMode,
    _validate_classification_pair,
    _validate_mcf_comment,
)
from .model_publications import ExportPublication
from .model_review_repairs import ReviewRepairSummary
from .model_scans import (
    AcceptedCreditsProvenance,
    AcceptedCreditsReference,
    ExportScanReference,
    ReviewMcfDocument,
    ReviewSynchronization,
    ReviewTargetRequest,
    SavedReviewSummary,
    Scan,
)


class ReviewScanMembership(ReviewWriteModel):
    scan_id: str
    role: ReviewScanRole


class ReviewScanReference(BaseModel):
    scan: Scan
    role: ReviewScanRole


class ReviewFilter(BaseModel):
    id: str
    source_scan_id: str
    source_finding_id: int | None = Field(default=None, gt=0, le=MAX_PERSISTED_INTEGER, strict=True)
    source: Literal["audio", "visual"]
    category: McfCategoryFamily | None
    subcategory: McfCategory | None
    severity: Severity | None = None
    mcf_channel: McfChannel | None = None
    mcf_comment: str | None = None
    label: str | None
    details: str | None
    group_key: str | None
    start_seconds: float
    end_seconds: float | None
    confidence: float | None
    edit_action: EditAction
    selected: bool

    @field_validator("mcf_comment")
    @classmethod
    def validate_mcf_comment(cls, value):
        return _validate_mcf_comment(value)

    @model_validator(mode="after")
    def validate_classification(self):
        _validate_classification_pair(self.category, self.subcategory, required=False)
        return self


class SavedReview(SavedReviewSummary):
    memberships: list[ReviewScanMembership]
    scans: list[ReviewScanReference]
    filters: list[ReviewFilter]
    base_filters: list[ReviewFilter] = Field(default_factory=list)
    policy_owner_id: int | None = None
    policy_revision: int = Field(default=0, ge=0)
    policy_missing_evidence: list[ReviewFilter] = Field(default_factory=list)
    policy_required_filter_ids: list[str] = Field(default_factory=list)
    policy_locked_severities: dict[str, Severity] = Field(default_factory=dict)
    owner_id: int | None = None
    original_author_id: int | None = None
    mcf_document: ReviewMcfDocument = Field(default_factory=ReviewMcfDocument)
    synchronization: ReviewSynchronization
    accepted_credits: AcceptedCreditsProvenance | None = None


class ReviewFilterInput(ReviewFilter, ReviewWriteModel):
    id: ReviewFilterId
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float | None = Field(allow_inf_nan=False)
    confidence: float | None = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
            raise ValueError("End time must be after start time")
        return self


class CreditsModelConsent(BaseModel):
    backend_id: str = Field(min_length=1)
    artifact_ids: list[str]
    expected_size: int = Field(ge=0)
    availability: Literal["available", "unavailable"]
    device: Literal["cpu", "cuda", "coreml", "metal", "mlx", "none"]
    license_spdx: str | None = None
    license_status: Literal["declared", "undeclared", "license-review-required"]


class CreditsDetectionStartRequest(ReviewWriteModel):
    target: ReviewTargetRequest
    qwen_model: str | None = None


class CreditsDetectionStartResult(BaseModel):
    scan: Scan
    queue_id: int
    model_consent: CreditsModelConsent | None = None


class CreditsImportAcceptanceContext(ReviewWriteModel):
    kind: Literal["import"]
    target: ReviewTargetRequest


class CreditsReviewAcceptanceContext(ReviewWriteModel):
    kind: Literal["review"]
    review_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    policy_owner_id: int | None = Field(default=None, gt=0, strict=True)
    policy_revision: int | None = Field(default=None, ge=0, strict=True)


CreditsAcceptanceContext = Annotated[
    CreditsImportAcceptanceContext | CreditsReviewAcceptanceContext,
    Field(discriminator="kind"),
]


class CreditsAcceptanceRequest(ReviewWriteModel):
    context: CreditsAcceptanceContext
    accepted_boundary_seconds: float = Field(ge=0, allow_inf_nan=False)


class CreditsAcceptanceResult(BaseModel):
    accepted_credits: AcceptedCreditsReference
    membership: ReviewScanMembership
    filter: ReviewFilterInput
    base_filter: ReviewFilterInput | None = None
    policy_owner_id: int | None = None
    policy_revision: int = Field(default=0, ge=0)


class CreditsModelConsentRequest(ReviewWriteModel):
    decision: Literal["acquire", "continue_without_qwen"]


class ReviewPolicyToken(ReviewWriteModel):
    policy_owner_id: int | None = Field(default=None, gt=0, strict=True)
    policy_revision: int | None = Field(default=None, ge=0, strict=True)


class FilterDefaultsApplyRequest(ReviewPolicyToken):
    filters: list[ReviewFilterInput]
    review_id: str | None = None


class FilterDefaultsApplyResult(ReviewWriteModel):
    filters: list[ReviewFilterInput]
    selection_changes: int = Field(ge=0)
    severity_changes: int = Field(ge=0)
    channel_changes: int = Field(ge=0)


class ReviewFilterDefaultsApplyResult(FilterDefaultsApplyResult):
    base_filters: list[ReviewFilterInput] = Field(default_factory=list)
    policy_owner_id: int | None = None
    policy_revision: int = Field(default=0, ge=0)


class NewManualReviewFilter(ReviewWriteModel):
    id: ReviewFilterId
    source: Literal["audio", "visual"]
    category: McfCategoryFamily
    subcategory: McfCategory
    severity: Severity | None = None
    mcf_channel: McfChannel | None = None
    mcf_comment: str | None = None
    label: str | None
    details: str | None
    group_key: str | None
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(allow_inf_nan=False)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    edit_action: EditAction
    selected: bool = True

    @field_validator("mcf_comment")
    @classmethod
    def validate_mcf_comment(cls, value):
        return _validate_mcf_comment(value)

    @model_validator(mode="after")
    def validate_range(self):
        _validate_classification_pair(self.category, self.subcategory, required=True)
        if self.end_seconds <= self.start_seconds:
            raise ValueError("End time must be after start time")
        return self


class McfMetadataScanSeed(ReviewWriteModel):
    kind: Literal["scan"]
    scan_id: str


class McfMetadataCatalogSeed(ReviewWriteModel):
    kind: Literal["catalog"]


McfMetadataSeedRequest = Annotated[
    McfMetadataScanSeed | McfMetadataCatalogSeed, Field(discriminator="kind")
]


class McfMetadataSeedCandidate(ReviewWriteModel):
    kind: Literal["scan", "catalog"]
    scan_id: str | None = None
    label: str
    mcf_document: ReviewMcfDocument

    @model_validator(mode="after")
    def validate_identity(self):
        if (self.kind == "scan") != (self.scan_id is not None):
            raise ValueError("Scan metadata candidates require exactly one scan ID")
        return self


class ReviewCreate(ReviewWriteModel):
    name: ReviewName
    memberships: list[ReviewScanMembership] = Field(default_factory=list)
    filters: list[ReviewFilterInput] | None = None
    new_manual_filters: list[NewManualReviewFilter] = Field(default_factory=list)
    mcf_metadata_seed: McfMetadataSeedRequest | None = None


class ReviewSave(ReviewPolicyToken):
    revision: ReviewRevision
    name: ReviewName
    memberships: list[ReviewScanMembership]
    filters: list[ReviewFilterInput]
    filter_basis: Literal["base", "effective"] = "effective"
    mcf_document: ReviewMcfDocument
    synchronization: ReviewSynchronization
    accepted_credits: AcceptedCreditsReference | None = None
    new_manual_filters: list[NewManualReviewFilter] = Field(default_factory=list)


class ReviewRetimeRequest(ReviewPolicyToken):
    revision: ReviewRevision
    memberships: list[ReviewScanMembership]
    current_synchronization: ReviewSynchronization
    proposed_synchronization: ReviewSynchronization
    mcf_document: ReviewMcfDocument
    filters: list[ReviewFilterInput]
    filter_basis: Literal["base", "effective"] = "effective"
    accepted_credits: AcceptedCreditsReference | None
    new_manual_filters: list[NewManualReviewFilter]


class ReviewRetimeResult(ReviewWriteModel):
    synchronization: ReviewSynchronization
    mcf_document: ReviewMcfDocument
    filters: list[ReviewFilterInput]
    base_filters: list[ReviewFilterInput] = Field(default_factory=list)
    accepted_credits: AcceptedCreditsReference | None
    new_manual_filters: list[NewManualReviewFilter]
    policy_owner_id: int | None = None
    policy_revision: int = Field(default=0, ge=0)


class ReviewRename(ReviewWriteModel):
    revision: ReviewRevision
    name: ReviewName


class ReviewDuplicate(ReviewRename, ReviewPolicyToken):
    pass


class ReviewExportRequest(ReviewPolicyToken):
    revision: ReviewRevision


class LibraryReviewCreate(ReviewCreate):
    media_source_id: str | None = None


class ReviewReference(BaseModel):
    id: str
    name: str
    updated_at: AwareDatetime


class ScanReferences(BaseModel):
    scan_id: str
    active_exports: list[ExportFormat] = Field(default_factory=list)
    reviews: list[ReviewReference]
    has_hidden_references: bool = False
    community_in_progress: bool = False


class ReviewExportResponse(BaseModel):
    format: ExportFormat
    review_id: str
    exported_format_revision: int = Field(ge=0)
    exported_at: AwareDatetime
    exported_scans: list[ExportScanReference]
    exported_review: SavedReviewSummary
    policy_owner_id: int | None = None
    policy_revision: int = Field(default=0, ge=0)
    publication: ExportPublication | None = None


class EdlImportOptions(ReviewWriteModel):
    target: ReviewTargetRequest
    name: ReviewName


class EdlImportWarning(BaseModel):
    code: Literal["unsupported_actions_ignored"]
    message: str
    ignored_action_count: int = Field(gt=0)


class EdlImportResult(BaseModel):
    review: SavedReview
    scan: Scan
    warning: EdlImportWarning | None


class McfImportOptions(ReviewWriteModel):
    target: ReviewTargetRequest
    name: ReviewName
    ignore_errors: bool = False
    synchronization: ReviewSynchronization
    mcf_material_end_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    accepted_credits: AcceptedCreditsReference | None = None

    @model_validator(mode="after")
    def validate_timing(self):
        """Require imported MCF material bounds to fit the synchronization interval."""
        if self.synchronization.end_mode == SynchronizationEndMode.BEFORE_CREDITS:
            if self.mcf_material_end_seconds != self.synchronization.end_seconds:
                raise ValueError("Before-credits material end must match synchronization end")
        elif self.mcf_material_end_seconds is not None and not (
            self.synchronization.start_seconds
            < self.mcf_material_end_seconds
            <= self.synchronization.end_seconds
        ):
            raise ValueError("File-end material end must be within the synchronization interval")
        return self


class McfMetadataNotice(BaseModel):
    code: Literal["metadata_mismatch"]
    fields: list[str]
    mcf_metadata: ReviewMcfDocument
    catalog_metadata: ReviewMcfDocument


class McfImportResult(BaseModel):
    review: SavedReview
    scan: Scan
    source_version: McfSourceVersion
    notices: list[McfMetadataNotice] = Field(default_factory=list)


class McfImportIssue(BaseModel):
    code: str
    message: str
    line_number: int
    line_text: str


class McfPreflightIssue(BaseModel):
    code: Literal[
        "missing_category",
        "missing_subcategory",
        "missing_severity",
        "invalid_category",
        "invalid_subcategory",
        "invalid_category_pair",
        "invalid_severity",
        "invalid_mcf_channel",
        "invalid_mcf_comment",
        "invalid_mcf_metadata",
        "invalid_material_bounds",
    ]
    field: str
    filter_id: UUID | None = None
    filter_position: int | None = Field(default=None, ge=0)
    message: str


class McfPreflight(BaseModel):
    review_id: str
    revision: ReviewRevision
    eligible: bool
    issues: list[McfPreflightIssue] = Field(default_factory=list)
    policy_owner_id: int | None = None
    policy_revision: int = Field(default=0, ge=0)


class ReviewErrorDetail(BaseModel):
    code: str
    message: str
    line_number: int | None = None
    line_text: str | None = None
    references: ScanReferences | None = None
    repair: ReviewRepairSummary | None = None
    reason: str | None = None
    transient: bool | None = None
    filter_ids: list[UUID] = Field(default_factory=list)
    document_fields: list[str] = Field(default_factory=list)
    scan_id: str | None = None
    candidate_id: str | None = None
    metadata_candidates: list[McfMetadataSeedCandidate] = Field(default_factory=list)
    import_issues: list[McfImportIssue] = Field(default_factory=list)


class ReviewError(BaseModel):
    detail: ReviewErrorDetail


class RequestValidationErrorResponse(BaseModel):
    """Path-safe shape returned by the global request-validation handler."""

    detail: list[dict[str, str | list[str | int]]]
