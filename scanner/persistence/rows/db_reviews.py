"""Saved review, synchronization, and accepted credits database rows."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    relationship,
    validates,
)

from scanner.models import (
    MCF_CATEGORY_TO_FAMILY,
    CreditsDetectionMethod,
    McfCategoryFamily,
    McfChannel,
    McfWorkType,
    Severity,
    SynchronizationEndMode,
    normalize_review_name,
)

from .db_core import (
    Base,
    _mcf_pair_constraint,
    _name_constraint,
    _string_enum,
)


class SavedReviewRow(Base):
    """One isolated, explicitly saved review of a physical media version."""

    __tablename__ = "saved_reviews"
    __table_args__ = (
        _name_constraint("name", required=True, name="ck_saved_reviews_name"),
        CheckConstraint("revision >= 0", name="ck_saved_reviews_revision"),
        CheckConstraint("content_revision >= 0", name="ck_saved_reviews_content_revision"),
        CheckConstraint("edl_content_revision >= 0", name="ck_saved_reviews_edl_revision"),
        CheckConstraint("mcf_content_revision >= 0", name="ck_saved_reviews_mcf_revision"),
        CheckConstraint(
            "(owner_id IS NULL) = (original_author_id IS NULL)", name="ck_saved_reviews_owner_pair"
        ),
        Index("ix_saved_reviews_target", "media_item_id", "media_source_id"),
        Index("ix_saved_reviews_owner_id", "owner_id"),
    )

    id = Column(String, primary_key=True)
    media_item_id = Column(String, ForeignKey("media_items.id"), nullable=False)
    media_source_id = Column(String, nullable=False, default="", server_default="")
    name = Column(String, nullable=False)
    revision = Column(Integer, nullable=False, default=0, server_default="0")
    content_revision = Column(Integer, nullable=False, default=0, server_default="0")
    edl_content_revision = Column(Integer, nullable=False, default=0, server_default="0")
    mcf_content_revision = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    # The current owner, who is also the policy owner. Null on both means awaiting owner setup.
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    original_author_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    media_item = relationship("MediaItemRow")
    memberships = relationship(
        "ReviewScanMembershipRow",
        back_populates="review",
        cascade="all, delete-orphan",
        order_by="ReviewScanMembershipRow.position",
    )
    filters = relationship(
        "ReviewFilterRow",
        back_populates="review",
        cascade="all, delete-orphan",
        order_by="ReviewFilterRow.position",
    )
    policy_evidence = relationship("ReviewPolicyEvidenceRow", cascade="all, delete-orphan")
    mcf_document = relationship(
        "ReviewMcfDocumentRow",
        back_populates="review",
        cascade="all, delete-orphan",
        uselist=False,
    )
    synchronization = relationship(
        "ReviewSynchronizationRow",
        back_populates="review",
        cascade="all, delete-orphan",
        uselist=False,
    )
    accepted_credits = relationship(
        "ReviewAcceptedCreditsRow",
        back_populates="review",
        cascade="all, delete-orphan",
        uselist=False,
    )
    community_sources = relationship(
        "ReviewCommunitySourceRow",
        back_populates="review",
        cascade="all, delete-orphan",
        order_by="ReviewCommunitySourceRow.position",
    )

    @validates("name")
    def validate_name(self, key, value):
        return normalize_review_name(value)

    @validates("media_source_id")
    def normalize_version(self, key, value):
        return value or ""


class ReviewScanMembershipRow(Base):
    __tablename__ = "review_scan_memberships"
    __table_args__ = (
        UniqueConstraint("review_id", "position", name="uq_review_memberships_position"),
        CheckConstraint("role IN ('audio', 'video', 'both')", name="ck_review_memberships_role"),
        CheckConstraint("position >= 0", name="ck_review_memberships_position"),
    )

    review_id = Column(String, ForeignKey("saved_reviews.id", ondelete="CASCADE"), primary_key=True)
    scan_id = Column(String, ForeignKey("scans.id"), primary_key=True)
    role = Column(String, nullable=False)
    position = Column(Integer, nullable=False)

    review = relationship("SavedReviewRow", back_populates="memberships")
    scan = relationship("ScanRow")


class ReviewPolicyEvidenceRow(Base):
    """Immutable first-bound intervals, independent of editable memberships."""

    __tablename__ = "review_policy_evidence"
    __table_args__ = (
        CheckConstraint("start_seconds >= 0", name="ck_review_policy_evidence_start"),
        CheckConstraint(
            "end_seconds IS NULL OR end_seconds > start_seconds",
            name="ck_review_policy_evidence_end",
        ),
        CheckConstraint(
            "sync_start >= 0 AND sync_end > sync_start", name="ck_review_policy_evidence_sync"
        ),
        CheckConstraint("retime IN (0, 1)", name="ck_review_policy_evidence_retime"),
    )

    review_id = Column(String, ForeignKey("saved_reviews.id", ondelete="CASCADE"), primary_key=True)
    source_finding_id = Column(Integer, ForeignKey("scan_findings.id"), primary_key=True)
    start_seconds = Column(Float, nullable=False)
    end_seconds = Column(Float, nullable=True)
    sync_start = Column(Float, nullable=False)
    sync_end = Column(Float, nullable=False)
    retime = Column(Boolean, nullable=False)


class ReviewFilterRow(Base):
    """Copied content, never an alias of editable source evidence.

    A filter comes from a scan (`source_scan_id`, and `source_finding_id` unless added
    by hand) or from an included community snapshot (`community_source_id` and the
    master's `community_filter_id`); otherwise both kinds behave the same.
    """

    __tablename__ = "review_filters"
    __table_args__ = (
        UniqueConstraint("review_id", "source_finding_id", name="uq_review_filters_finding"),
        UniqueConstraint("review_id", "position", name="uq_review_filters_position"),
        ForeignKeyConstraint(
            ["source_finding_id", "source_scan_id"],
            ["scan_findings.id", "scan_findings.scan_id"],
            name="fk_review_filters_source_finding_scan",
        ),
        ForeignKeyConstraint(
            ["review_id", "community_source_id"],
            ["review_community_sources.review_id", "review_community_sources.id"],
            name="fk_review_filters_community_source",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "review_id",
            "community_source_id",
            "community_filter_id",
            name="uq_review_filters_community_filter",
        ),
        CheckConstraint(
            "(source_scan_id IS NULL) = (community_source_id IS NOT NULL) "
            "AND (community_source_id IS NULL) = (community_filter_id IS NULL) "
            "AND (community_source_id IS NULL OR source_finding_id IS NULL)",
            name="ck_review_filters_provenance",
        ),
        CheckConstraint("position >= 0", name="ck_review_filters_position"),
        CheckConstraint("source IN ('audio', 'visual')", name="ck_review_filters_source"),
        CheckConstraint("edit_action IN ('mute', 'skip')", name="ck_review_filters_action"),
        CheckConstraint("start_seconds >= 0", name="ck_review_filters_start"),
        CheckConstraint(
            "end_seconds IS NULL OR end_seconds > start_seconds", name="ck_review_filters_end"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_review_filters_confidence"),
        _mcf_pair_constraint("ck_review_filters_classification_pair"),
        CheckConstraint(
            "mcf_channel IS NULL OR mcf_channel IN ('both', 'video', 'audio')",
            name="ck_review_filters_mcf_channel",
        ),
        CheckConstraint(
            "mcf_comment IS NULL OR (length(mcf_comment) > 0 "
            "AND instr(mcf_comment, char(10)) = 0 AND instr(mcf_comment, char(13)) = 0 "
            "AND instr(mcf_comment, '-->') = 0)",
            name="ck_review_filters_mcf_comment",
        ),
    )

    review_id = Column(String, ForeignKey("saved_reviews.id", ondelete="CASCADE"), primary_key=True)
    id = Column(String, primary_key=True)
    source_scan_id = Column(String, ForeignKey("scans.id"), nullable=True)
    source_finding_id = Column(Integer, nullable=True)
    position = Column(Integer, nullable=False)
    source = Column(String, nullable=False)
    category = Column(_string_enum(McfCategoryFamily), nullable=True)
    subcategory = Column(String, nullable=True)
    severity = Column(_string_enum(Severity), nullable=True)
    mcf_channel = Column(_string_enum(McfChannel), nullable=True)
    mcf_comment = Column(Text, nullable=True)
    label = Column(String, nullable=True)
    details = Column(Text, nullable=True)
    group_key = Column(String, nullable=True)
    start_seconds = Column(Float, nullable=False)
    end_seconds = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    edit_action = Column(String, nullable=False)
    selected = Column(Boolean, nullable=False, default=True)
    manually_added = Column(Boolean, nullable=True)
    manually_edited = Column(Boolean, nullable=True)
    community_source_id = Column(String(36), nullable=True)
    community_filter_id = Column(String(128), nullable=True)

    review = relationship("SavedReviewRow", back_populates="filters")
    source_scan = relationship("ScanRow", foreign_keys=[source_scan_id])
    # Evidence is read-only through this relationship: saves explicitly copy scalar fields.
    source_finding = relationship("ScanFindingRow", viewonly=True)

    @validates("subcategory")
    def validate_subcategory(self, key, value):
        if value is not None and value not in MCF_CATEGORY_TO_FAMILY:
            raise ValueError("Unknown MCF subcategory")
        return value


class ReviewMcfDocumentRow(Base):
    __tablename__ = "review_mcf_documents"
    __table_args__ = (
        CheckConstraint("year IS NULL OR year >= 0", name="ck_review_mcf_year"),
        CheckConstraint("season IS NULL OR season > 0", name="ck_review_mcf_season"),
        CheckConstraint("episode IS NULL OR episode > 0", name="ck_review_mcf_episode"),
        CheckConstraint(
            "material_start_seconds IS NULL OR material_start_seconds >= 0",
            name="ck_review_mcf_start",
        ),
        CheckConstraint(
            "material_end_seconds IS NULL OR material_end_seconds > 0",
            name="ck_review_mcf_positive_end",
        ),
        CheckConstraint(
            "material_start_seconds IS NULL OR material_end_seconds IS NULL OR "
            "material_end_seconds > material_start_seconds",
            name="ck_review_mcf_end",
        ),
    )

    review_id = Column(String, ForeignKey("saved_reviews.id", ondelete="CASCADE"), primary_key=True)
    title = Column(String, nullable=True)
    year = Column(Integer, nullable=True)
    work_type = Column(_string_enum(McfWorkType), nullable=True)
    season = Column(Integer, nullable=True)
    episode = Column(Integer, nullable=True)
    source_url = Column(Text, nullable=True)
    imdb_url = Column(Text, nullable=True)
    release = Column(String, nullable=True)
    comment = Column(Text, nullable=True)
    material_start_seconds = Column(Float, nullable=True)
    material_end_seconds = Column(Float, nullable=True)

    review = relationship("SavedReviewRow", back_populates="mcf_document")


class ReviewSynchronizationRow(Base):
    __tablename__ = "review_synchronizations"
    __table_args__ = (
        CheckConstraint(
            "start_seconds >= 0 AND start_seconds < 1e308",
            name="ck_review_synchronization_start",
        ),
        CheckConstraint(
            "end_seconds > start_seconds AND end_seconds < 1e308",
            name="ck_review_synchronization_end",
        ),
        CheckConstraint(
            "end_mode IN ('before_credits', 'file_end')",
            name="ck_review_synchronization_end_mode",
        ),
    )

    review_id = Column(String, ForeignKey("saved_reviews.id", ondelete="CASCADE"), primary_key=True)
    start_seconds = Column(Float, nullable=False)
    end_seconds = Column(Float, nullable=False)
    end_mode = Column(_string_enum(SynchronizationEndMode), nullable=False)

    review = relationship("SavedReviewRow", back_populates="synchronization")


class ReviewAcceptedCreditsRow(Base):
    __tablename__ = "review_accepted_credits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_finding_id", "scan_id"],
            ["scan_findings.id", "scan_findings.scan_id"],
            name="fk_review_accepted_credits_finding_scan",
        ),
        CheckConstraint("length(candidate_id) > 0", name="ck_review_accepted_credits_candidate"),
        CheckConstraint(
            "detected_boundary_seconds >= 0 AND detected_boundary_seconds < 1e308",
            name="ck_review_accepted_credits_detected_boundary",
        ),
        CheckConstraint(
            "accepted_boundary_seconds >= 0 AND accepted_boundary_seconds < 1e308",
            name="ck_review_accepted_credits_accepted_boundary",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_review_accepted_credits_confidence",
        ),
        CheckConstraint(
            "method IN ('chapters', 'visual_text', 'hybrid', 'hybrid_qwen')",
            name="ck_review_accepted_credits_method",
        ),
    )

    review_id = Column(String, ForeignKey("saved_reviews.id", ondelete="CASCADE"), primary_key=True)
    scan_id = Column(String, nullable=False)
    source_finding_id = Column(Integer, nullable=False)
    candidate_id = Column(String, nullable=False)
    detected_boundary_seconds = Column(Float, nullable=False)
    accepted_boundary_seconds = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    method = Column(_string_enum(CreditsDetectionMethod), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=False)

    review = relationship("SavedReviewRow", back_populates="accepted_credits")
    source_finding = relationship("ScanFindingRow", viewonly=True)
