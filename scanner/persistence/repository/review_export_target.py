"""Physical media and evidence validation shared by review format preparations."""

import os
from pathlib import Path

from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import ReviewScanMembershipRow, SavedReviewRow, ScanMediaItemAssociationRow, ScanRow
from scanner.repository import ReviewMembershipError

from .community_import_targets import matches_target, validate_target_role, verified_target
from .repository_paths import resolve_local_path


def _review_export_scans(
    db: Session, review: SavedReviewRow
) -> tuple[list[ReviewScanMembershipRow], dict[str, ScanRow], Path]:
    """Validate export memberships and return their shared physical media path."""
    memberships = list(review.memberships)
    if not memberships:
        raise ReviewMembershipError("A review must include at least one scan")
    target_path = None
    if review.media_item.path:
        try:
            target_path = Path(resolve_local_path(db, review.media_item.path).local_path).resolve()
        except ValueError as exc:
            raise ReviewMembershipError("The review media file is unavailable") from exc
    scans: dict[str, ScanRow] = {}
    physical_path: Path | None = None
    for membership in memberships:
        scan = db.get(ScanRow, membership.scan_id)
        if scan is None or scan.status != "complete":
            raise ReviewMembershipError("A review scan is unavailable")
        if scan.media_item_id != review.media_item_id:
            associated = (
                db.query(ScanMediaItemAssociationRow)
                .filter(
                    ScanMediaItemAssociationRow.scan_id == scan.id,
                    ScanMediaItemAssociationRow.media_item_id == review.media_item_id,
                )
                .first()
            )
            if associated is None:
                raise ReviewMembershipError("A review scan is not associated with its target")
        association = verified_target(db, scan, review.media_item_id, review.media_source_id)
        validate_target_role(association, membership.role)
        if (
            scan.community_import is not None
            and scan.community_import.artifact_bytes
            and association is None
        ):
            raise ReviewMembershipError("A community scan is not verified for this target")
        if association is None and (scan.media_source_id or "") != review.media_source_id:
            raise ReviewMembershipError("Review scans must resolve to one media version")
        scan_path = (
            Path(association.resolved_local_path).resolve()
            if association
            else root._resolved_scan_path(scan)
        )
        if not scan_path.is_file() or not os.access(scan_path, os.R_OK):
            raise ReviewMembershipError("The review media file is unavailable")
        if target_path is not None and scan_path != target_path:
            raise ReviewMembershipError(
                "Review scans must resolve to the target physical media file"
            )
        matches = (
            matches_target(association, target_path or scan_path)
            if association
            else root._scan_matches_physical_version(scan, target_path or scan_path)
        )
        if not matches:
            raise ReviewMembershipError("Review scans must match the target physical media version")
        if physical_path is None:
            physical_path = scan_path
        elif scan_path != physical_path:
            raise ReviewMembershipError("Review scans must resolve to the same physical media file")
        scans[scan.id] = scan
    assert physical_path is not None
    return memberships, scans, physical_path
