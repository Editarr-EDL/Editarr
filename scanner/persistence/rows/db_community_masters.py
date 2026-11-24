"""Community master filter sets downloaded by one account, with local review state (0037)."""

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
    false,
    text,
)

from .db_core import Base


class CommunityMasterLinkRow(Base):
    """One account's link from a local media version to a community version's master set.

    Each account has one live link per local version and service; a replaced link
    keeps its filter rows as history.
    """

    __tablename__ = "community_master_links"
    __table_args__ = (
        CheckConstraint(
            "alignment_state IN ('not_required', 'required', 'approved')",
            name="ck_community_master_links_alignment_state",
        ),
        CheckConstraint(
            "link_state IN ('active', 'merged', 'unavailable', 'replaced')",
            name="ck_community_master_links_link_state",
        ),
        CheckConstraint(
            "match_percent IS NULL OR (match_percent >= 0 AND match_percent <= 100)",
            name="ck_community_master_links_match_percent",
        ),
        CheckConstraint(
            "applied_revision >= 1 AND latest_revision >= applied_revision",
            name="ck_community_master_links_revisions",
        ),
        Index(
            "uq_community_master_links_live",
            "owner_id",
            "media_version_id",
            "service_url",
            unique=True,
            sqlite_where=text("link_state != 'replaced'"),
        ),
        Index("ix_community_master_links_version", "owner_id", "community_version_id"),
    )

    id = Column(String(36), primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    media_version_id = Column(String, ForeignKey("media_versions.id"), nullable=False)
    service_url = Column(String(256), nullable=False)
    contributor_id = Column(String(128), nullable=True)
    community_version_id = Column(String(128), nullable=False)
    community_work_id = Column(String(128), nullable=True)
    label = Column(String(256), nullable=True)
    alias_id = Column(String(128), nullable=True)
    version_status = Column(String(16), nullable=False)
    tier = Column(String(8), nullable=True)
    match_percent = Column(Integer, nullable=True)
    applied_revision = Column(Integer, nullable=False)
    latest_revision = Column(Integer, nullable=False)
    alignment_state = Column(String(16), nullable=False)
    alignment_json = Column(Text, nullable=True)
    alignment_revision = Column(Integer, nullable=True)
    link_state = Column(String(16), nullable=False)
    merged_into = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class CommunityMasterSnapshotRow(Base):
    """Exact verified snapshot bytes, signature and receipt for one revision of a link."""

    __tablename__ = "community_master_snapshots"
    __table_args__ = (
        UniqueConstraint("link_id", "revision", name="uq_community_master_snapshots_revision"),
    )

    id = Column(String(36), primary_key=True)
    link_id = Column(
        String(36), ForeignKey("community_master_links.id", ondelete="CASCADE"), nullable=False
    )
    revision = Column(Integer, nullable=False)
    snapshot_digest = Column(String(64), nullable=False)
    snapshot_bytes = Column(LargeBinary, nullable=False)
    signature_json = Column(Text, nullable=False)
    service_key_id = Column(String(71), nullable=False)
    receipt_json = Column(Text, nullable=False)
    receipt_id = Column(String(64), nullable=False)
    downloaded_at = Column(DateTime, nullable=False)


class CommunityMasterFilterRow(Base):
    """A master filter's accepted baseline and this account's local decision about it.

    `present` is whether the row is in the local set: the master serves it, or the
    user kept it after the master removed it. `baseline_json` holds the material
    master values last accepted, including the master's own `present`.
    """

    __tablename__ = "community_master_filters"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'verified', 'adjusted', 'rejected', 'low_confidence')",
            name="ck_community_master_filters_state",
        ),
        CheckConstraint(
            "local_start_ms IS NULL OR local_start_ms >= 0",
            name="ck_community_master_filters_start",
        ),
        CheckConstraint(
            "local_start_ms IS NULL OR local_end_ms IS NULL OR local_start_ms < local_end_ms",
            name="ck_community_master_filters_bounds",
        ),
        CheckConstraint(
            "my_vote IS NULL OR my_vote IN ('up', 'down')",
            name="ck_community_master_filters_vote",
        ),
    )

    link_id = Column(
        String(36), ForeignKey("community_master_links.id", ondelete="CASCADE"), primary_key=True
    )
    filter_id = Column(String(128), primary_key=True)
    category = Column(String(64), nullable=False)
    baseline_json = Column(Text, nullable=False)
    baseline_revision = Column(Integer, nullable=False)
    info_json = Column(Text, nullable=False)
    local_start_ms = Column(Integer, nullable=True)
    local_end_ms = Column(Integer, nullable=True)
    local_subcategory = Column(String(128), nullable=True)
    local_severity = Column(String(16), nullable=True)
    local_action = Column(String(16), nullable=True)
    state = Column(String(16), nullable=False)
    locally_verified = Column(Boolean, nullable=False, server_default=false())
    local_touched = Column(Boolean, nullable=False, server_default=false())
    my_vote = Column(String(4), nullable=True)
    present = Column(Boolean, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class CommunityMasterUpdateRow(Base):
    """An undecided difference between a filter's baseline and the latest master revision."""

    __tablename__ = "community_master_updates"
    __table_args__ = (
        CheckConstraint(
            "change IN ('new', 'changed', 'removed', 'low_confidence')",
            name="ck_community_master_updates_change",
        ),
    )

    link_id = Column(
        String(36), ForeignKey("community_master_links.id", ondelete="CASCADE"), primary_key=True
    )
    filter_id = Column(String(128), primary_key=True)
    to_revision = Column(Integer, nullable=False)
    change = Column(String(16), nullable=False)
    changed_fields_json = Column(Text, nullable=False)
    new_master_json = Column(Text, nullable=False)
    merged_into = Column(String(128), nullable=True)
    values_digest = Column(String(64), nullable=False)


class CommunityMasterDismissalRow(Base):
    """Hides one filter's update until the master values differ from the dismissed ones."""

    __tablename__ = "community_master_dismissals"

    link_id = Column(
        String(36), ForeignKey("community_master_links.id", ondelete="CASCADE"), primary_key=True
    )
    filter_id = Column(String(128), primary_key=True)
    values_digest = Column(String(64), nullable=False)
    dismissed_at = Column(DateTime, nullable=False)


class CommunityMasterPreferenceRow(Base):
    """An account's master-set display preferences; absent means the defaults."""

    __tablename__ = "community_master_preferences"
    __table_args__ = (
        CheckConstraint(
            "confidence_threshold >= 0 AND confidence_threshold <= 1",
            name="ck_community_master_preferences_threshold",
        ),
    )

    owner_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    confidence_threshold = Column(Float, nullable=False, server_default=text("0"))
    show_low_confidence = Column(Boolean, nullable=False, server_default=false())


class CommunitySyncStateRow(Base):
    """An account's daily master-set sync with one community service."""

    __tablename__ = "community_sync_state"
    __table_args__ = (
        CheckConstraint(
            "state IN ('idle', 'running', 'failed')", name="ck_community_sync_state_state"
        ),
    )

    owner_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    service_url = Column(String(256), primary_key=True)
    contributor_id = Column(String(128), nullable=True)
    state = Column(String(16), nullable=False)
    run_id = Column(String(36), nullable=True)
    last_started_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    next_due_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(64), nullable=True)


class CommunitySyncNotificationRow(Base):
    """A sync finding about one link: a new revision, a merge, or an unavailable version."""

    __tablename__ = "community_sync_notifications"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('changed', 'merged', 'unavailable')",
            name="ck_community_sync_notifications_kind",
        ),
        UniqueConstraint(
            "owner_id", "link_id", "to_revision", name="uq_community_sync_notifications_revision"
        ),
        Index("ix_community_sync_notifications_unread", "owner_id", "read_at"),
    )

    id = Column(String(36), primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    link_id = Column(
        String(36), ForeignKey("community_master_links.id", ondelete="CASCADE"), nullable=False
    )
    kind = Column(String(16), nullable=False)
    from_revision = Column(Integer, nullable=True)
    to_revision = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False)
    read_at = Column(DateTime, nullable=True)


class RetiredCommunityOperationRow(Base):
    """A revision-protocol receipt or vote intent, kept verbatim after 0037 retired it.

    Only backup, export and audit read it. It names no scan by foreign key, so it never
    blocks a scan deletion.
    """

    __tablename__ = "retired_community_operations"

    operation_id = Column(String(128), primary_key=True)
    service_url = Column(String(256), nullable=False)
    kind = Column(String(16), nullable=False)
    state = Column(String(24), nullable=False)
    contributor_id = Column(String(128), nullable=True)
    installation_id = Column(String(36), nullable=True)
    contribution_id = Column(String(128), nullable=True)
    revision_id = Column(String(128), nullable=True)
    digest = Column(String(71), nullable=True)
    scan_id = Column(String(), nullable=True)
    consent_version = Column(String(128), nullable=True)
    receipt = Column(String(2048), nullable=True)
    vote = Column(String(16), nullable=True)
    updated_at = Column(String(64), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    retired_at = Column(String(64), nullable=False)
    retired_reason = Column(String(64), nullable=False)
