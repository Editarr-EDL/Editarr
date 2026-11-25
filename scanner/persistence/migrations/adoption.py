"""Coordinated, recoverable migration of existing local SQLite databases."""

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from scanner.backup.snapshots import (
    DATABASE_NAME,
    create_safety_snapshot,
    validate_safety_snapshot,
)
from scanner.persistence.migrations.foreign_keys import REPAIR_REVISION, foreign_key_failures
from scanner.persistence.migrations.runner import run_packaged, upgrade_connection
from scanner.persistence.migrations.schema import verify_frozen_baseline, verify_versioned_schema
from scanner.runtime.maintenance import DatabaseMaintenance
from scanner.security.community_identity import (
    IdentityBackupSnapshot,
    read_identity_backup_snapshot,
)
from scanner.security.community_identity_binding import projects_identity
from scanner.security.community_identity_homes import (
    owner_identity_root,
    publish_incoming_identity,
    recovery_destination,
)

BASELINE_REVISION = "0001_current_baseline"
PREVIOUS_REVISION = "0002_scan_provenance"
COMMUNITY_REVISION = "0003_local_community"
MEDIA_IDENTITY_REVISION = "0004_media_identity"
AUDIO_TRACK_REVISION = "0005_audio_tracks"
COMMUNITY_EVIDENCE_REVISION = "0006_community_evidence"
PUBLICATIONS_REVISION = "0007_community_publications"
API_KEY_REVISION = "0008_api_key"
READABLE_API_KEY_REVISION = "0009_readable_api_key"
USER_ACCOUNTS_REVISION = "0010_user_accounts"
ACTIVITY_REVISION = "0011_activity_ownership"
PERSONAL_FILTER_REVISION = "0012_personal_filter_values"
COMMUNITY_IDENTITY_REVISION = "0013_community_identities"
MEDIA_ACCESS_REVISION = "0014_media_access_exceptions"
SEALED_API_KEY_REVISION = "0015_sealed_api_keys"
PERSONAL_FILTER_RULES_REVISION = "0016_personal_filter_rules"
USER_PROFILES_REVISION = "0017_user_profiles"
JELLYFIN_SYNC_REVISION = "0018_jellyfin_account_sync"
REVIEW_POLICY_REVISION = "0019_review_policy_evidence"
EXPORT_SNAPSHOT_REVISION = "0021_export_snapshots"
EXPORT_REGENERATION_REVISION = "0022_export_regenerations"
ACCOUNT_LINK_REVISION = "0023_account_link_transitions"
ACCOUNT_DISPOSITION_REVISION = "0024_account_dispositions"
ARCHIVE_ARR_REVISION = "0025_archive_arr_sources"
ARCHIVE_MANUAL_REVISION = "0026_archive_manual_files"
JELLYFIN_OWNER_REVISION = "0027_jellyfin_owner"
JELLYFIN_ADMISSION_REVISION = "0028_jellyfin_admission"
JELLYFIN_BLOCKS_REVISION = "0029_jellyfin_user_blocks"
REMOVE_PASSWORDS_REVISION = "0030_remove_passwords"
EXPLICIT_FILTER_MODES_REVISION = "0031_explicit_filter_modes"
MANUAL_FILTER_TRACKING_REVISION = "0032_manual_filter_tracking"
SCAN_WORKER_TIMING_REVISION = "0033_scan_worker_timing"
SCAN_READ_INDEXES_REVISION = "0034_scan_read_indexes"
STABLE_MEDIA_IDENTITY_REVISION = "0035_stable_media_identity"
SAMPLED_MEDIA_IDENTITY_REVISION = "0036_sampled_media_identity"
COMMUNITY_MASTER_REVISION = "0037_community_master_sets"
RECEIPT_CAPABILITIES_REVISION = "0038_retire_receipt_capabilities"
HEAD_REVISION = RECEIPT_CAPABILITIES_REVISION
JOURNAL_NAME = "migration-upgrade.json"


def _record(data_dir: Path, state: dict) -> None:
    """Atomically persist migration state before the first database write."""
    path = Path(data_dir) / JOURNAL_NAME
    descriptor, pending = tempfile.mkstemp(prefix=".migration-upgrade-", dir=data_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(state, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, path)
        if os.name == "posix":
            directory = os.open(data_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def _inspect_database(path: Path) -> str | None:
    """Classify a file in read-only mode, including its required seed inventory."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("An existing regular SQLite database is required")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as database:
        if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")
        summary = foreign_key_failures(database)
        if summary:
            raise ValueError(f"SQLite foreign key check failed: {summary}")
        tables = {
            row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if database.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('view', 'trigger') LIMIT 1"
        ).fetchone():
            raise ValueError("Unsupported database schema: unexpected view or trigger")
        if not tables:
            raise ValueError("Existing empty database requires explicit fresh migration")
        if not {"filter_settings", "mcf_severity_defaults"} <= tables:
            raise ValueError("Unsupported database schema: required baseline tables are missing")
        revision = None
        if "alembic_version" in tables:
            revisions = database.execute("SELECT version_num FROM alembic_version").fetchall()
            if revisions not in (
                [(BASELINE_REVISION,)],
                [(PREVIOUS_REVISION,)],
                [(COMMUNITY_REVISION,)],
                [(MEDIA_IDENTITY_REVISION,)],
                [(AUDIO_TRACK_REVISION,)],
                [(COMMUNITY_EVIDENCE_REVISION,)],
                [(PUBLICATIONS_REVISION,)],
                [(API_KEY_REVISION,)],
                [(READABLE_API_KEY_REVISION,)],
                [(USER_ACCOUNTS_REVISION,)],
                [(ACTIVITY_REVISION,)],
                [(PERSONAL_FILTER_REVISION,)],
                [(COMMUNITY_IDENTITY_REVISION,)],
                [(MEDIA_ACCESS_REVISION,)],
                [(SEALED_API_KEY_REVISION,)],
                [(PERSONAL_FILTER_RULES_REVISION,)],
                [(USER_PROFILES_REVISION,)],
                [(JELLYFIN_SYNC_REVISION,)],
                [(REVIEW_POLICY_REVISION,)],
                [(REPAIR_REVISION,)],
                [(EXPORT_SNAPSHOT_REVISION,)],
                [(EXPORT_REGENERATION_REVISION,)],
                [(ACCOUNT_LINK_REVISION,)],
                [(ACCOUNT_DISPOSITION_REVISION,)],
                [(ARCHIVE_ARR_REVISION,)],
                [(ARCHIVE_MANUAL_REVISION,)],
                [(JELLYFIN_OWNER_REVISION,)],
                [(JELLYFIN_ADMISSION_REVISION,)],
                [(JELLYFIN_BLOCKS_REVISION,)],
                [(REMOVE_PASSWORDS_REVISION,)],
                [(EXPLICIT_FILTER_MODES_REVISION,)],
                [(MANUAL_FILTER_TRACKING_REVISION,)],
                [(SCAN_WORKER_TIMING_REVISION,)],
                [(SCAN_READ_INDEXES_REVISION,)],
                [(STABLE_MEDIA_IDENTITY_REVISION,)],
                [(SAMPLED_MEDIA_IDENTITY_REVISION,)],
                [(COMMUNITY_MASTER_REVISION,)],
                [(HEAD_REVISION,)],
            ):
                raise ValueError(f"Unsupported database revision: {revisions!r}")
            revision = revisions[0][0]
    engine = create_engine(
        "sqlite://", creator=lambda: sqlite3.connect(uri, uri=True), poolclass=NullPool
    )
    try:
        with engine.connect() as connection:
            if revision is None:
                verify_frozen_baseline(connection)
            else:
                verify_versioned_schema(connection, revision)
    finally:
        engine.dispose()
    return revision


def _stamp(connection) -> None:
    run_packaged(command.stamp, connection, BASELINE_REVISION)


def upgrade_existing_database(
    database_path: Path, data_dir: Path, *, timeout: float = 10.0
) -> Path | None:
    """Adopt only the frozen baseline; return the recoverable pre-upgrade generation."""
    database_path, data_dir = Path(database_path), Path(data_dir)
    with DatabaseMaintenance(data_dir).exclusive(timeout=timeout):
        journal = data_dir / JOURNAL_NAME
        if journal.exists():
            state = json.loads(journal.read_text(encoding="utf-8"))
            if state.get("database") != str(database_path.resolve()):
                raise RuntimeError("Upgrade journal belongs to another database")
            if state.get("status") != "complete":
                raise RuntimeError(
                    "Previous upgrade unresolved; inspect journal and recover deliberately"
                )
        revision = _inspect_database(database_path)
        if revision == HEAD_REVISION:
            return None
        generation = create_safety_snapshot(database_path, data_dir)
        validate_safety_snapshot(generation)
        state = {
            "status": "intent",
            "database": str(database_path.resolve()),
            "generation": str(generation.resolve()),
            "from_revision": revision or "unversioned-v1",
            "to_revision": HEAD_REVISION,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _record(data_dir, state)
        engine = create_engine(f"sqlite:///{database_path}")
        try:
            with engine.begin() as connection:
                if revision is None:
                    _stamp(connection)
                upgrade_connection(connection)
            if revision is None or revision < REMOVE_PASSWORDS_REVISION:
                _discard_freed_pages(database_path)
            _inspect_database(database_path)
        except BaseException:
            state["status"] = "failed"
            _record(data_dir, state)
            raise
        finally:
            engine.dispose()
        state["status"] = "complete"
        _record(data_dir, state)
        return generation


def _discard_freed_pages(database_path: Path) -> None:
    """Rewrite the file so no page keeps bytes of a deleted row, such as a password hash.

    VACUUM cannot run inside the migration's transaction, so it runs after it commits.
    """
    with closing(sqlite3.connect(database_path, isolation_level=None)) as database:
        database.execute("VACUUM")


def _carried_identity(
    database_path: Path, data_dir: Path, generation_database: Path, recovery_dir: Path
) -> IdentityBackupSnapshot | None:
    """Read the owner's identity for the recovery directory's incoming directory.

    A generation from before revision 0013 cannot name the home the files moved to,
    so only the incoming directory is adoptable after its upgrade. The generation's
    connections must accept the identity exactly as the identity loader will; a key
    changed since then has history the files no longer prove, so it is refused.
    """
    root = owner_identity_root(data_dir, database_path)
    if root is None:
        return None
    if recovery_dir.resolve() == data_dir.resolve():
        raise ValueError("Recovery destination must be outside the active data directory")
    snapshot = read_identity_backup_snapshot(data_dir, root)
    uri = f"{generation_database.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as database:
        rows = (
            database.execute(
                "SELECT service_url, contributor_id, installation_id, key_id "
                "FROM community_connections"
            ).fetchall()
            if database.execute("PRAGMA table_info(community_connections)").fetchone()
            else []
        )
    identity = snapshot.identity
    for service_url, contributor_id, installation_id, key_id in rows:
        if contributor_id is None:
            bound = installation_id in (None, identity.installation_id)
        else:
            bound = projects_identity(
                identity,
                installation_id=installation_id,
                contributor_id=contributor_id,
                service_url=service_url,
                key_id=key_id,
            )
        if not bound:
            raise ValueError(
                "The community identity differs from the one the recovery generation used"
            )
    return snapshot


def export_upgrade_recovery(database_path: Path, data_dir: Path, destination: Path) -> Path:
    """Export a verified prior generation and its identity beside it; never touch active data."""
    database_path, data_dir, destination = Path(database_path), Path(data_dir), Path(destination)
    with DatabaseMaintenance(data_dir).exclusive():
        state = json.loads((data_dir / JOURNAL_NAME).read_text(encoding="utf-8"))
        if state.get("database") != str(database_path.resolve()) or state.get("status") not in {
            "intent",
            "failed",
            "complete",
        }:
            raise ValueError("No matching recorded recovery generation")
        generation = Path(state["generation"])
        if generation.parent != (data_dir / "migration-snapshots").resolve():
            raise ValueError("Recovery generation is outside the snapshot directory")
        validate_safety_snapshot(generation)
        identity = _carried_identity(
            database_path, data_dir, generation / DATABASE_NAME, destination.parent
        )
        with recovery_destination(destination.parent):
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(destination)
            if identity is not None:
                publish_incoming_identity(destination.parent, identity.files)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            try:
                source_uri = f"{(generation / DATABASE_NAME).as_uri()}?mode=ro"
                with closing(sqlite3.connect(source_uri, uri=True)) as source:
                    with closing(sqlite3.connect(destination)) as target:
                        source.backup(target)
                _inspect_database(destination)
            except BaseException:
                destination.unlink()
                raise
        return destination
