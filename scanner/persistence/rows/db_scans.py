"""Scan, queue, finding, and export database rows."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import (
    relationship,
    synonym,
    validates,
)

from scanner.models import (
    MCF_CATEGORY_TO_FAMILY,
    ExportFormat,
    McfCategoryFamily,
    McfChannel,
    McfSourceVersion,
    McfWorkType,
    ScanMode,
    Severity,
    normalize_scan_name,
)

from .db_core import (
    Base,
    _mcf_pair_constraint,
    _name_constraint,
    _string_enum,
)
from .db_policies import ExportScope


class ScanRow(Base):
    """A single Editarr scan; scan_number is monotonic per media item."""

    __tablename__ = "scans"
    __table_args__ = (
        UniqueConstraint("media_item_id", "scan_number"),
        _name_constraint("name", required=False, name="ck_scans_name"),
        CheckConstraint("origin IN ('generated', 'manual', 'imported')", name="ck_scans_origin"),
        CheckConstraint("scan_mode IN ('content', 'credits_detection')", name="ck_scans_mode"),
        CheckConstraint(
            "(initiator_id IS NULL) = (initiator_permission_revision IS NULL) AND "
            "(initiator_permission_revision IS NULL OR initiator_permission_revision >= 1)",
            name="ck_scans_initiator_pair",
        ),
        Index("ix_scans_initiator_id", "initiator_id"),
    )

    id = Column(String, primary_key=True)
    media_item_id = Column(String, ForeignKey("media_items.id"), nullable=False)
    scan_number = Column(Integer, nullable=False)
    name = Column(String, nullable=True)
    origin = Column(String, nullable=False, default="generated", server_default="generated")
    import_source_name = Column(String, nullable=True)
    imported_at = Column(DateTime(timezone=True), nullable=True)
    scan_mode = Column(
        _string_enum(ScanMode),
        nullable=False,
        default=ScanMode.CONTENT,
        server_default=ScanMode.CONTENT.value,
    )
    source_path = Column(String, nullable=False)
    media_source_id = Column(String, nullable=True)
    media_source_name = Column(String, nullable=True)
    resolved_local_path = Column(String, nullable=False)
    video_hash = Column(String, nullable=True)
    scene_name = Column(String, nullable=True)
    release_group = Column(String, nullable=True)
    media_format = Column(String, nullable=True)
    media_provider_id = Column(String, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="queued")
    scan_audio = Column(Boolean, nullable=False, default=True)
    audio_model = Column(String, nullable=True)
    audio_filter = Column(String, nullable=True)
    scan_video = Column(Boolean, nullable=False, default=True)
    video_model = Column(String, nullable=True)
    frame_interval = Column(Float, nullable=True)
    speed_up = Column(Boolean, nullable=False, default=False)
    qwen_model = Column(String, nullable=False, default="qwen3-vl-8b-instruct-4bit")
    qwen_summaries_per_segment = Column(Integer, nullable=False, default=4)
    qwen_segment_join_gap_seconds = Column(Float, nullable=False, default=3.0)
    error_message = Column(Text, nullable=True)
    selection_revision = Column(Integer, nullable=False, default=0)
    progress_percent = Column(Float, nullable=True)
    progress_stage = Column(String, nullable=True)
    progress_message = Column(String, nullable=True)
    worker_pid = Column(Integer, nullable=True)
    worker_pgid = Column(Integer, nullable=True)
    worker_token = Column(String, nullable=True)
    worker_started_at = Column(DateTime, nullable=True)
    worker_heartbeat_at = Column(DateTime, nullable=True)
    visual_checkpoint_complete = Column(Boolean, nullable=False, default=False)
    audio_extraction_checkpoint_complete = Column(Boolean, nullable=False, default=False)
    audio_checkpoint_complete = Column(Boolean, nullable=False, default=False)
    checkpoint_stage = Column(String, nullable=False, default="queued")
    checkpoint_state = Column(Text, nullable=False, default="{}")
    checkpoint_updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    release_version = Column(String, nullable=True)
    scanner_behavior_version = Column(String, nullable=True)
    build_revision = Column(String, nullable=True)
    audio_model_revision = Column(String, nullable=True)
    video_model_revision = Column(String, nullable=True)
    requested_audio_selection_json = Column(Text, nullable=True)
    analyzed_audio_selection_json = Column(Text, nullable=True)
    # Who started the job or created the record, and their permission revision then.
    initiator_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    initiator_permission_revision = Column(Integer, nullable=True)
    worker_elapsed_seconds = Column(Float, nullable=True)
    worker_attempt_started_at = Column(DateTime, nullable=True)

    media_item = relationship("MediaItemRow")
    mcf_document = relationship(
        "ScanMcfDocumentRow",
        back_populates="scan",
        cascade="all, delete-orphan",
        uselist=False,
    )
    credits_detection = relationship(
        "CreditsDetectionRunRow",
        back_populates="scan",
        cascade="all, delete-orphan",
        uselist=False,
    )
    stage_coverage = relationship(
        "ScanStageCoverageRow", back_populates="scan", cascade="all, delete-orphan"
    )
    community_import = relationship(
        "CommunityImportRow", uselist=False, cascade="all, delete-orphan"
    )

    @validates("name")
    def validate_name(self, key, value):
        return normalize_scan_name(value)


class ScanMediaItemAssociationRow(Base):
    """An explicit user-approved reuse of a scan for another media item."""

    __tablename__ = "scan_media_item_associations"
    __table_args__ = (UniqueConstraint("scan_id", "media_item_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_id = Column(String, ForeignKey("scans.id"), nullable=False)
    media_item_id = Column(String, ForeignKey("media_items.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    scan = relationship("ScanRow")
    media_item = relationship("MediaItemRow")


class ScanStageCoverageRow(Base):
    """Recorded outcome for one stage attempt; absent rows mean unknown."""

    __tablename__ = "scan_stage_coverage"
    __table_args__ = (
        CheckConstraint("length(stage) > 0", name="ck_scan_stage_coverage_stage"),
        CheckConstraint("length(execution_id) > 0", name="ck_scan_stage_coverage_execution"),
        CheckConstraint(
            "status IN ('complete', 'partial', 'failed', 'disabled', 'unsupported', 'unknown')",
            name="ck_scan_stage_coverage_status",
        ),
        CheckConstraint(
            "source IN ('generated', 'manual', 'imported')",
            name="ck_scan_stage_coverage_source",
        ),
        CheckConstraint(
            "sampling_seconds IS NULL OR (sampling_seconds > 0 AND sampling_seconds < 1e308)",
            name="ck_scan_stage_coverage_sampling",
        ),
        CheckConstraint(
            "intervals_json IS NULL OR (length(intervals_json) <= 16384 "
            "AND json_valid(intervals_json))",
            name="ck_scan_stage_coverage_intervals",
        ),
        CheckConstraint(
            "effective_settings_json IS NULL OR (length(effective_settings_json) <= 16384 "
            "AND json_valid(effective_settings_json))",
            name="ck_scan_stage_coverage_settings",
        ),
    )

    scan_id = Column(String, ForeignKey("scans.id", ondelete="CASCADE"), primary_key=True)
    stage = Column(String, primary_key=True)
    execution_id = Column(String, primary_key=True)
    status = Column(String, nullable=False)
    source = Column(String, nullable=False)
    release_version = Column(String, nullable=True)
    scanner_behavior_version = Column(String, nullable=True)
    build_revision = Column(String, nullable=True)
    model_id = Column(String, nullable=True)
    model_revision = Column(String, nullable=True)
    model_digest = Column(String, nullable=True)
    quantization = Column(String, nullable=True)
    effective_settings_json = Column(Text, nullable=True)
    intervals_json = Column(Text, nullable=True)
    sampling_seconds = Column(Float, nullable=True)

    scan = relationship("ScanRow", back_populates="stage_coverage")


class MediaItemExportRow(Base):
    """One export file of a physical media source/version, format, and scope.

    A personal export has a target user and the export ID its path uses. The actor
    published it; the policy owner and original author describe the source review.
    A legacy record with no actor is awaiting owner setup.
    """

    __tablename__ = "media_item_exports"
    __table_args__ = (
        CheckConstraint("format IN ('edl', 'mcf')", name="ck_media_item_exports_format"),
        CheckConstraint(
            "(review_id IS NULL AND exported_format_revision IS NULL) OR "
            "(review_id IS NOT NULL AND exported_format_revision IS NOT NULL "
            "AND exported_format_revision >= 0)",
            name="ck_media_item_exports_review_provenance",
        ),
        CheckConstraint(
            "(scope = 'personal') = (target_user_id IS NOT NULL) "
            "AND (target_user_id IS NULL) = (target_export_id IS NULL)",
            name="ck_media_item_exports_target",
        ),
        CheckConstraint(
            "target_export_id IS NULL OR (length(target_export_id) = 38 "
            "AND substr(target_export_id, 1, 6) = 'local-' "
            "AND substr(target_export_id, 7) NOT GLOB '*[^0-9a-f]*') OR "
            "(length(target_export_id) = 32 AND target_export_id NOT GLOB '*[^0-9a-f]*' "
            "AND target_export_id != '00000000000000000000000000000000')",
            name="ck_media_item_exports_target_export_id",
        ),
        CheckConstraint(
            "(policy_owner_id IS NULL) = (original_author_id IS NULL)",
            name="ck_media_item_exports_authorship_pair",
        ),
        CheckConstraint(
            "scope = 'legacy' OR (actor_id IS NOT NULL AND policy_owner_id IS NOT NULL "
            "AND policy_revision IS NOT NULL)",
            name="ck_media_item_exports_provenance",
        ),
        CheckConstraint(
            "actor_id IS NOT NULL OR policy_owner_id IS NULL",
            name="ck_media_item_exports_awaiting_owner",
        ),
        CheckConstraint(
            "policy_revision IS NULL OR policy_revision >= 0",
            name="ck_media_item_exports_policy_revision",
        ),
        Index(
            "uq_media_item_exports_personal",
            "media_item_id",
            "media_source_id",
            "format",
            "target_user_id",
            unique=True,
            sqlite_where=text("scope = 'personal'"),
        ),
        Index(
            "uq_media_item_exports_shared",
            "media_item_id",
            "media_source_id",
            "format",
            "scope",
            unique=True,
            sqlite_where=text("scope != 'personal'"),
        ),
        Index("ix_media_item_exports_policy_owner_id", "policy_owner_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    media_item_id = Column(String, ForeignKey("media_items.id"), nullable=False)
    # An empty value represents an item without a version ID.
    media_source_id = Column(String, nullable=False, default="")
    format = Column(_string_enum(ExportFormat), nullable=False, default=ExportFormat.EDL)
    exported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    review_id = Column(String, ForeignKey("saved_reviews.id"), nullable=True)
    exported_format_revision = Column(Integer, nullable=True)
    # Unscoped writes describe legacy adjacent sidecars.
    scope = Column(_string_enum(ExportScope), nullable=False, default=ExportScope.LEGACY)
    target_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    target_export_id = Column(String, nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    policy_owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    original_author_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    policy_revision = Column(Integer, nullable=True)
    content_bytes = Column(LargeBinary, nullable=True)
    snapshot_json = Column(Text, nullable=True)
    destination_path = Column(String, nullable=True)
    publication_revision = Column(Integer, nullable=False, default=0, server_default="0")
    # The account whose policy a publication detached from its review follows when it
    # differs from the stamped `policy_owner_id`: a deleted review whose owner changed
    # before regeneration finished, or a transferred account's files (0024).
    intended_policy_owner_id = Column(Integer, nullable=True)

    media_item = relationship("MediaItemRow")
    review = relationship("SavedReviewRow")
    scans = relationship(
        "MediaItemExportScanRow", back_populates="export", cascade="all, delete-orphan"
    )
    regeneration = relationship(
        "ExportRegenerationRow",
        back_populates="export",
        uselist=False,
        cascade="all, delete-orphan",
    )


class MediaItemExportScanRow(Base):
    """One scan and its role in an active per-version format export."""

    __tablename__ = "media_item_export_scans"
    __table_args__ = (
        UniqueConstraint("media_item_export_id", "scan_id"),
        CheckConstraint("role IN ('audio', 'video', 'both')", name="ck_export_scans_role"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    media_item_export_id = Column(
        Integer, ForeignKey("media_item_exports.id", ondelete="CASCADE"), nullable=False
    )
    media_item_edl_id = synonym("media_item_export_id")
    scan_id = Column(String, ForeignKey("scans.id"), nullable=False)
    role = Column(String, nullable=False)

    export = relationship("MediaItemExportRow", back_populates="scans")
    edl = synonym("export")
    scan = relationship("ScanRow")


class ScanQueueRow(Base):
    __tablename__ = "scan_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_id = Column(String, ForeignKey("scans.id"), nullable=False, unique=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    scan = relationship("ScanRow")


class ScanFindingRow(Base):
    __tablename__ = "scan_findings"
    __table_args__ = (
        UniqueConstraint("id", "scan_id", name="uq_scan_findings_id_scan"),
        _mcf_pair_constraint("ck_scan_findings_classification_pair"),
        CheckConstraint(
            "mcf_channel IS NULL OR mcf_channel IN ('both', 'video', 'audio')",
            name="ck_scan_findings_mcf_channel",
        ),
        CheckConstraint(
            "mcf_comment IS NULL OR (length(mcf_comment) > 0 "
            "AND instr(mcf_comment, char(10)) = 0 AND instr(mcf_comment, char(13)) = 0 "
            "AND instr(mcf_comment, '-->') = 0)",
            name="ck_scan_findings_mcf_comment",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_id = Column(String, ForeignKey("scans.id"), nullable=False)
    category = Column(_string_enum(McfCategoryFamily), nullable=True)
    subcategory = Column(String, nullable=True)
    severity = Column(_string_enum(Severity), nullable=True)
    mcf_channel = Column(_string_enum(McfChannel), nullable=True)
    mcf_comment = Column(Text, nullable=True)
    start_seconds = Column(Float, nullable=False)
    end_seconds = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    label = Column(String, nullable=True)
    details = Column(Text, nullable=True)
    source = Column(String, nullable=False)
    selected = Column(Boolean, nullable=False, default=True)
    edit_action = Column(String, nullable=False, default="skip")
    group_key = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    manually_added = Column(Boolean, nullable=True)
    manually_edited = Column(Boolean, nullable=True)

    scan = relationship("ScanRow")

    @validates("subcategory")
    def validate_subcategory(self, key, value):
        if value is not None and value not in MCF_CATEGORY_TO_FAMILY:
            raise ValueError("Unknown MCF subcategory")
        return value


class ScanMcfDocumentRow(Base):
    __tablename__ = "scan_mcf_documents"
    __table_args__ = (
        CheckConstraint("year IS NULL OR year >= 0", name="ck_scan_mcf_year"),
        CheckConstraint("season IS NULL OR season > 0", name="ck_scan_mcf_season"),
        CheckConstraint("episode IS NULL OR episode > 0", name="ck_scan_mcf_episode"),
        CheckConstraint("material_start_seconds >= 0", name="ck_scan_mcf_start"),
        CheckConstraint("material_end_seconds > material_start_seconds", name="ck_scan_mcf_end"),
    )

    scan_id = Column(String, ForeignKey("scans.id", ondelete="CASCADE"), primary_key=True)
    source_version = Column(_string_enum(McfSourceVersion), nullable=False)
    title = Column(String, nullable=True)
    year = Column(Integer, nullable=True)
    work_type = Column(_string_enum(McfWorkType), nullable=True)
    season = Column(Integer, nullable=True)
    episode = Column(Integer, nullable=True)
    source_url = Column(Text, nullable=True)
    imdb_url = Column(Text, nullable=True)
    release = Column(String, nullable=True)
    comment = Column(Text, nullable=True)
    material_start_seconds = Column(Float, nullable=False)
    material_end_seconds = Column(Float, nullable=False)

    scan = relationship("ScanRow", back_populates="mcf_document")


class CreditsDetectionRunRow(Base):
    __tablename__ = "credits_detection_runs"
    __table_args__ = (
        CheckConstraint(
            "length(configuration_json) > 0", name="ck_credits_detection_configuration"
        ),
        CheckConstraint("length(detector_version) > 0", name="ck_credits_detection_version"),
        CheckConstraint(
            "cache_key IS NULL OR length(cache_key) > 0", name="ck_credits_detection_cache"
        ),
        CheckConstraint(
            "result_json IS NULL OR length(result_json) > 0", name="ck_credits_detection_result"
        ),
    )

    scan_id = Column(String, ForeignKey("scans.id", ondelete="CASCADE"), primary_key=True)
    configuration_json = Column(Text, nullable=False)
    detector_version = Column(String, nullable=False)
    cache_key = Column(String, nullable=True)
    result_json = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    scan = relationship("ScanRow", back_populates="credits_detection")
