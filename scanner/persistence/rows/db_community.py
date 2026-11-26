"""Queryable community metadata; private signing keys remain in private files."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
)

from .db_core import Base


class CommunityIdentityRow(Base):
    """Names the private directory that holds one account's community signing identity.

    `credential_ref` is random and never derived from the account, so two accounts
    cannot resolve to the same directory. A null `user_id` awaits owner setup.
    """

    __tablename__ = "community_identities"
    __table_args__ = (
        CheckConstraint(
            "length(credential_ref) = 32 AND credential_ref NOT GLOB '*[^0-9a-f]*'",
            name="ck_community_identities_credential_ref",
        ),
    )

    credential_ref = Column(String(32), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class CommunityConnectionRow(Base):
    __tablename__ = "community_connections"
    __table_args__ = (
        CheckConstraint("length(service_url) > 0", name="ck_community_connection_service"),
        CheckConstraint("sharing_enabled IN (0, 1)", name="ck_community_connection_sharing"),
    )

    credential_ref = Column(
        String(32), ForeignKey("community_identities.credential_ref"), primary_key=True
    )
    service_url = Column(String(256), primary_key=True)
    contributor_id = Column(String(128), nullable=True)
    installation_id = Column(String(36), nullable=True)
    key_id = Column(String(71), nullable=True)
    sharing_enabled = Column(Boolean, nullable=False, server_default=false())
    consent_version = Column(String(128), nullable=True)
    consented_at = Column(String(64), nullable=True)


class CommunityImportRow(Base):
    """A revision-protocol download, retired by 0037; its scan stays a plain imported scan."""

    __tablename__ = "retired_community_imports"
    __table_args__ = (
        UniqueConstraint(
            "service_url", "revision_id", "digest", name="uq_community_import_revision"
        ),
    )

    id = Column(String(36), primary_key=True)
    scan_id = Column(
        String(), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    service_url = Column(String(256), nullable=False)
    contribution_id = Column(String(128), nullable=False)
    revision_id = Column(String(128), nullable=False)
    digest = Column(String(71), nullable=False)
    author_id = Column(String(128), nullable=False)
    artifact_type = Column(String(32), nullable=False)
    artifact_bytes = Column(LargeBinary, nullable=True)
    manifest_bytes = Column(LargeBinary, nullable=True)
    signature_bytes = Column(LargeBinary, nullable=True)
    historical_key_json = Column(Text, nullable=True)
    provenance_json = Column(Text, nullable=True)
    event_ids_json = Column(Text, nullable=True)
    receipt_json = Column(Text, nullable=True)


class CommunityOperationRow(Base):
    __tablename__ = "community_operations"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('publish', 'submission', 'filter_vote')", name="ck_community_operation_kind"
        ),
        CheckConstraint(
            "state IN ('pending', 'submitted', 'acknowledged', 'failed')",
            name="ck_community_operation_state",
        ),
        Index("ix_community_operation_state", "kind", "state"),
        Index("ix_community_operations_owner_id", "owner_id"),
    )

    operation_id = Column(String(128), primary_key=True)
    service_url = Column(String(256), nullable=False)
    kind = Column(String(16), nullable=False)
    state = Column(String(24), nullable=False)
    contributor_id = Column(String(128), nullable=True)
    installation_id = Column(String(36), nullable=True)
    contribution_id = Column(String(128), nullable=True)
    revision_id = Column(String(128), nullable=True)
    digest = Column(String(71), nullable=True)
    scan_id = Column(String(), ForeignKey("scans.id", ondelete="RESTRICT"), nullable=True)
    consent_version = Column(String(128), nullable=True)
    receipt = Column(String(2048), nullable=True)
    vote = Column(String(16), nullable=True)
    updated_at = Column(String(64), nullable=False)
    # The account whose contributor history this is; null awaits owner setup.
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Who started it; null when nobody recorded that, as for every pre-0013 row.
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Master-set vote intents (0037); the exact vote JSON stays in `receipt`.
    community_version_id = Column(String(128), nullable=True)
    filter_id = Column(String(128), nullable=True)
    receipt_id = Column(String(64), nullable=True)


class CommunityImportTargetRow(Base):
    """A retired import's approved physical target, kept by 0037 for its imported scan."""

    __tablename__ = "retired_community_import_targets"
    import_id = Column(
        String(36),
        ForeignKey("retired_community_imports.id", ondelete="CASCADE"),
        primary_key=True,
    )
    media_item_id = Column(
        String, ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    media_source_id = Column(String, primary_key=True)
    resolved_local_path = Column(String, nullable=False)
    video_hash = Column(String, nullable=True)
    target_evidence_json = Column(Text, nullable=False)
    review_required = Column(Boolean, nullable=False)


class CommunityPublicationRow(Base):
    """Exact approved publication evidence retained independently of network outcomes."""

    __tablename__ = "community_publications"
    operation_id = Column(
        String(128),
        ForeignKey("community_operations.operation_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    artifact_bytes = Column(LargeBinary, nullable=False)
    manifest_bytes = Column(LargeBinary, nullable=False)
    signature_bytes = Column(LargeBinary, nullable=False)
    manifest_digest = Column(String(64), nullable=False)
    signature_digest = Column(String(64), nullable=False)
    preview_digest = Column(String(64), nullable=False)
    consent_token = Column(String(64), nullable=False)
    author_key_id = Column(String(71), nullable=False)
    source_kind = Column(String(16), nullable=False)
    source_id = Column(String, nullable=False)
    cancel_requested = Column(Boolean, nullable=False, server_default=false())
    error_code = Column(String(64), nullable=True)
    result_json = Column(Text, nullable=True)
