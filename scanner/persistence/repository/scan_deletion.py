"""Reference-aware scan deletion under one SQLite writer admission."""

from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import (
    CommunityImportRow,
    CommunityOperationRow,
    ReviewPolicyEvidenceRow,
    ScanFindingRow,
    ScanMediaItemAssociationRow,
    ScanQueueRow,
    ScanRow,
)
from scanner.repository import ScanReferencedError

from ..write_lock import write_lock


def delete_scan(db: Session, scan_id: str) -> bool:
    """Delete unused imports and detach finished operations without deleting their history."""
    with write_lock(db):
        try:
            row = db.get(ScanRow, scan_id)
            if row is None:
                return False
            references = root.get_scan_references(db, scan_id)
            if references.community_in_progress:
                raise ScanReferencedError(
                    "A community operation for this scan is still in progress", references
                )
            if references.reviews or references.active_exports:
                if references.active_exports and not references.reviews:
                    formats = ", ".join(value.value.upper() for value in references.active_exports)
                    raise ScanReferencedError(
                        f"Delete the active {formats} export before deleting its scan", references
                    )
                names = ", ".join(
                    f"{reference.name} ({reference.updated_at.isoformat()})"
                    for reference in references.reviews
                )
                message = "Scan is referenced by saved reviews"
                if names:
                    message += f": {names}"
                if references.active_exports:
                    formats = ", ".join(value.value.upper() for value in references.active_exports)
                    message += f"; delete the active {formats} export before deleting its scan"
                raise ScanReferencedError(message, references)
            _remove_rows(db, row)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise


def _remove_rows(db: Session, row: ScanRow) -> None:
    scan_id = row.id
    db.query(ScanQueueRow).filter_by(scan_id=scan_id).delete()
    db.query(ReviewPolicyEvidenceRow).filter(
        ReviewPolicyEvidenceRow.source_finding_id.in_(
            db.query(ScanFindingRow.id).filter_by(scan_id=scan_id)
        )
    ).delete(synchronize_session=False)
    db.query(CommunityImportRow).filter_by(scan_id=scan_id).delete()
    db.query(CommunityOperationRow).filter_by(scan_id=scan_id).update({"scan_id": None})
    db.query(ScanFindingRow).filter_by(scan_id=scan_id).delete()
    db.query(ScanMediaItemAssociationRow).filter_by(scan_id=scan_id).delete()
    db.delete(row)


def remove_unreferenced_scan(db: Session, scan_id: str) -> bool:
    """Stage removal of a scan nothing references; return False and keep it otherwise.

    Call inside the caller's writer transaction after staging any review deletions,
    so references from surviving reviews, exports, and pending community work keep it.
    """
    row = db.get(ScanRow, scan_id)
    references = root.get_scan_references(db, scan_id)
    if references.reviews or references.active_exports or references.community_in_progress:
        return False
    _remove_rows(db, row)
    return True
