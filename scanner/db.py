"""Database row compatibility imports and session management."""

# Import row modules before metadata creation and expose their public names here.
# ruff: noqa: F401

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.orm import Session, sessionmaker

from scanner.persistence import archive as _archive  # noqa: F401  # installs the archive read guard
from scanner.persistence.migrations.preflight import prepare_database
from scanner.persistence.rows.db_account_dispositions import (
    AccountDispositionRow,
    AccountTransferMoveRow,
)
from scanner.persistence.rows.db_account_links import (
    AccountLinkExportMoveRow,
    AccountLinkTransitionRow,
)
from scanner.persistence.rows.db_archive import (
    ArchivedConnectionRow,
    ArchivedMediaAccessRow,
    ArchivedSource,
)
from scanner.persistence.rows.db_audio_tracks import AudioTrackSelectionRow
from scanner.persistence.rows.db_audit import AuditEventRow
from scanner.persistence.rows.db_auth import (
    ApiKeyRow,
    AuthSessionRow,
    AuthSettingsRow,
    JellyfinAccountSyncRow,
    JellyfinUserBlockRow,
    RoleCapabilityDefaultRow,
    UserCapabilityOverrideRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.persistence.rows.db_catalog import (
    FilterSelectionRuleRow,
    FilterSettingsRow,
    JellyfinSettingsRow,
    McfSeverityDefaultRow,
    MediaItemRow,
    PathMappingRow,
)
from scanner.persistence.rows.db_community import (
    CommunityConnectionRow,
    CommunityIdentityRow,
    CommunityImportRow,
    CommunityImportTargetRow,
    CommunityOperationRow,
    CommunityPublicationRow,
)
from scanner.persistence.rows.db_community_masters import (
    CommunityMasterDismissalRow,
    CommunityMasterFilterRow,
    CommunityMasterLinkRow,
    CommunityMasterPreferenceRow,
    CommunityMasterSnapshotRow,
    CommunityMasterUpdateRow,
    CommunitySyncNotificationRow,
    CommunitySyncStateRow,
    RetiredCommunityOperationRow,
)
from scanner.persistence.rows.db_core import Base
from scanner.persistence.rows.db_export_regenerations import ExportRegenerationRow
from scanner.persistence.rows.db_media_identity import (
    MediaTitleAliasRow,
    MediaTitleRow,
    MediaTitleSourceRow,
    MediaVersionLocatorRow,
    MediaVersionRow,
    ReviewMediaVersionRow,
    ScanMediaVersionRow,
)
from scanner.persistence.rows.db_policies import (
    ExportScope,
    FilterEnforcementKind,
    FilterPreferenceMode,
    MediaAccessScopeRow,
    MediaAccessScopeSourceRow,
    MediaSourceKind,
    UserFilterCopyRuleRow,
    UserFilterCopySeverityRow,
    UserFilterEnforcementRow,
    UserFilterPersonalRuleRow,
    UserFilterPreferenceRow,
    UserFilterRuleOverrideRow,
    UserFilterSeverityOverrideRow,
)
from scanner.persistence.rows.db_review_community import ReviewCommunitySourceRow
from scanner.persistence.rows.db_review_repairs import ReviewScanRepairRow
from scanner.persistence.rows.db_reviews import (
    ReviewAcceptedCreditsRow,
    ReviewFilterRow,
    ReviewMcfDocumentRow,
    ReviewPolicyEvidenceRow,
    ReviewScanMembershipRow,
    ReviewSynchronizationRow,
    SavedReviewRow,
)
from scanner.persistence.rows.db_scans import (
    CreditsDetectionRunRow,
    MediaItemExportRow,
    MediaItemExportScanRow,
    ScanFindingRow,
    ScanMcfDocumentRow,
    ScanMediaItemAssociationRow,
    ScanQueueRow,
    ScanRow,
    ScanStageCoverageRow,
)
from scanner.persistence.seeds import (
    seed_community_identity,
    seed_filter_defaults,
    seed_media_access_default,
)
from scanner.runtime.maintenance import DatabaseMaintenance
from scanner.runtime.runtime_paths import ensure_user_private_directory
from scanner.settings.config import settings


@event.listens_for(Base.metadata, "after_create")
def _seed_filter_settings(_target, connection, **_kwargs):
    """Seed singleton filter settings and ordered severity defaults after schema creation."""
    seed_filter_defaults(connection)
    seed_media_access_default(connection)
    seed_community_identity(connection)


def initialize_sqlite_connection(connection, _record):
    """Enable and verify runtime FK enforcement before a SQLite transaction starts."""
    connection.execute("PRAGMA foreign_keys=ON")
    if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
        raise RuntimeError("Runtime SQLite foreign key enforcement is required")


def get_engine():
    ensure_user_private_directory(settings.data_dir)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    maintenance = DatabaseMaintenance(settings.data_dir)
    url = make_url(settings.database_url)
    database_path = (
        Path(url.database)
        if url.get_backend_name() == "sqlite" and url.database not in (None, ":memory:")
        else None
    )

    @event.listens_for(engine, "connect")
    def initialize_database_connection(connection, record):
        if url.get_backend_name() == "sqlite":
            initialize_sqlite_connection(connection, record)
        if database_path is not None:
            record.info["database_generation"] = database_path.stat().st_ino

    @event.listens_for(engine, "checkout")
    def acquire_db_lease(_connection, record, _proxy):
        fd = maintenance.checkout()
        if fd is not None:
            record.info["maintenance_fd"] = fd
        if (
            database_path is not None
            and record.info.get("database_generation") != database_path.stat().st_ino
        ):
            if fd is not None:
                maintenance.checkin(fd)
                record.info.pop("maintenance_fd", None)
            raise DisconnectionError("Database generation changed during restore")

    @event.listens_for(engine, "checkin")
    def release_db_lease(_connection, record):
        fd = record.info.pop("maintenance_fd", None)
        if fd is not None:
            maintenance.checkin(fd)

    return engine


def get_session_factory():
    return sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)


def init_db():
    prepare_database()


def get_db() -> Session:
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()
