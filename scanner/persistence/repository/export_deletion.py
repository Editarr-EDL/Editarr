"""Explicit deletion of a canonical publication, keeping its evidence while the path is absent."""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from scanner.db import MediaItemExportRow
from scanner.persistence.rows.db_export_regenerations import (
    ExportRegenerationRow,
    RegenerationFailure,
    RegenerationState,
)
from scanner.persistence.rows.db_policies import ExportScope
from scanner.security.access.grants import PrincipalAccess
from scanner.security.audit.actions import AuditAction

from .export_publication import ExportConflict, _check_destination, _event, safe_destination
from .export_recovery import (
    OWNED_DIGEST,
    committed_export_content,
    owned_digest,
    recoverable_sidecar,
    retained_path_absent,
)
from .export_regeneration import finish, save_snapshot


def record_deletion(row: MediaItemExportRow) -> bool:
    """Withdraw `row` as deleted, keeping the digest of its last written bytes.

    A deleted record is never regenerated, so a reappearing owned file is still
    recognized and removed. Returns False when `row` already records the deletion.
    """
    job = row.regeneration
    if job is not None and job.deleted:
        return False
    snapshot = json.loads(row.snapshot_json)
    if (owned := owned_digest(row)) is not None:
        snapshot[OWNED_DIGEST] = owned
    save_snapshot(row, snapshot)
    row.content_bytes = None
    if job is None:
        job = row.regeneration = ExportRegenerationRow(
            attempts=0, policy_owner_id=row.policy_owner_id, policy_revision=row.policy_revision
        )
    finish(job, RegenerationState.WITHDRAWN, RegenerationFailure.DELETED)
    return True


def _retained(db: Session, row: MediaItemExportRow) -> Path:
    """Resolve the retained destination without requiring the media; refuse foreign bytes."""
    from scanner.formats.export_layout import parse_export_id

    export_id = parse_export_id(row.target_export_id) if row.target_export_id else None
    snapshot = json.loads(row.snapshot_json)
    destination = safe_destination(
        Path(snapshot["media_path"]), row.format, export_id, require_media=False
    )
    _check_destination(db, row, destination)
    return destination


def removal_journal(db: Session, row: MediaItemExportRow, destination: Path):
    """Remove `destination` recoverably until the caller commits `row`'s deletion."""
    export_id = row.id
    return recoverable_sidecar(
        destination,
        None,
        journaled=True,
        committed_content=lambda: committed_export_content(db, export_id),
    )


def _remove(db: Session, row: MediaItemExportRow, destination: Path, stage) -> None:
    with removal_journal(db, row, destination):
        try:
            stage()
            db.commit()
        except BaseException:
            db.rollback()
            raise


def remove_returned_file(db: Session, row: MediaItemExportRow) -> None:
    """Remove a deleted record's owned bytes that reappeared at its retained path.

    Call under the publication and writer locks. Foreign bytes raise untouched.
    """
    _remove(db, row, _retained(db, row), lambda: None)


def delete_export(
    db: Session,
    row: MediaItemExportRow,
    revision: int,
    access: PrincipalAccess,
    *,
    publication_id: str,
) -> None:
    """Delete a freshly authorized publication, retaining rollback evidence until commit.

    The retained path decides: owned bytes are removed without requiring the media, and
    ENOENT there records a deleted record that keeps the ownership evidence. Repeating
    the request on that record changes nothing.
    """
    if not db.info.get("export_publication_lock"):
        raise ValueError("Acquire publication_lock before the authorized writer transaction")
    if row.scope == ExportScope.LEGACY or row.snapshot_json is None:
        raise ExportConflict("legacy_export_preserved", "Legacy exports require explicit migration")
    snapshot = json.loads(row.snapshot_json)
    if row.publication_revision != revision or snapshot["publication_id"] != publication_id:
        raise ExportConflict("stale_export_revision", "The export changed; reload it")
    destination = stage_removal(db, row, access)
    if destination is None:
        db.commit()
        return
    with removal_journal(db, row, destination):
        try:
            db.commit()
        except BaseException:
            db.rollback()
            raise


def stage_removal(
    db: Session,
    row: MediaItemExportRow,
    access: PrincipalAccess,
    *,
    review_revision: int | None = None,
) -> Path | None:
    """Stage deleting an authorized publication and its audit event without committing.

    Review deletion passes its confirmed `review_revision` for the audit details.
    Returns the retained path whose owned bytes the caller removes through
    `removal_journal` around its commit. When that path is ENOENT, the row instead
    becomes a deleted record that keeps its ownership evidence, and None is returned.
    """
    if retained_path_absent(Path(row.destination_path)):
        if record_deletion(row):
            _event(db, row, access, AuditAction.EXPORT_DELETE, review_revision=review_revision)
        return None
    destination = _retained(db, row)
    _event(db, row, access, AuditAction.EXPORT_DELETE, review_revision=review_revision)
    db.delete(row)
    return destination
