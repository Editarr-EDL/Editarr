"""Physical media and evidence validation shared by review format preparations."""

import os
from pathlib import Path

from sqlalchemy.orm import Session

from scanner.db import ReviewScanMembershipRow, SavedReviewRow, ScanMediaItemAssociationRow, ScanRow
from scanner.repository import ReviewMembershipError

from .community_import_targets import validate_target_role, verified_target
from .media_identity_store import review_version_id, scan_version_id
from .repository_paths import resolve_local_path
from .review_scan_paths import review_scan_location
from .review_version_evidence import review_file_matches
from .version_reuse import source_pending_reuse


def _review_export_scans(
    db: Session, review: SavedReviewRow
) -> tuple[list[ReviewScanMembershipRow], dict[str, ScanRow], Path]:
    """Validate export memberships and return their shared physical media path.

    A review without scans exports only when it includes community filter snapshots
    with filters; its media is then the review target's own file (see
    `_community_only_media`).
    """
    memberships = list(review.memberships)
    if not memberships:
        return [], {}, _community_only_media(db, review)
    target_path = None
    if review.media_item.path:
        try:
            target_path = Path(resolve_local_path(db, review.media_item.path).local_path).resolve()
        except ValueError as exc:
            raise ReviewMembershipError("The review media file is unavailable") from exc
    scans: dict[str, ScanRow] = {}
    physical_path: Path | None = None
    version_id = review_version_id(db, review.id)
    for membership in memberships:
        scan = db.get(ScanRow, membership.scan_id)
        if scan is None or scan.status != "complete" or source_pending_reuse(scan):
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
        if association is None and version_id:
            source_id = scan_version_id(db, scan.id)
            if source_id is not None and source_id != version_id:
                raise ReviewMembershipError("Review scans must match the original Editarr version")
        scan_path, matches = review_scan_location(scan, target_path, association)
        if not scan_path.is_file() or not os.access(scan_path, os.R_OK):
            raise ReviewMembershipError("The review media file is unavailable")
        if target_path is not None and scan_path != target_path:
            raise ReviewMembershipError(
                "Review scans must resolve to the target physical media file"
            )
        if not matches:
            raise ReviewMembershipError("Review scans must match the target physical media version")
        if physical_path is None:
            physical_path = scan_path
        elif scan_path != physical_path:
            raise ReviewMembershipError("Review scans must resolve to the same physical media file")
        scans[scan.id] = scan
    assert physical_path is not None
    if review_file_matches(db, review.id, physical_path) is False:
        raise ReviewMembershipError("The original Editarr review version is unavailable")
    return memberships, scans, physical_path


def _community_only_media(db: Session, review: SavedReviewRow) -> Path:
    """The readable target file of a review whose only evidence is community snapshots.

    Refuses a review with neither scans nor usable snapshot filters (misclassified ones
    are never exported), and a file that no longer matches the review's bound version.
    """
    from .review_community_export import classified
    from .review_community_sources import review_filters

    if not any(classified(item) for item in review_filters(review)):
        raise ReviewMembershipError("A review must include at least one scan or community filter")
    if not review.media_item.path:
        raise ReviewMembershipError("The review media file is unavailable")
    try:
        path = Path(resolve_local_path(db, review.media_item.path).local_path).resolve()
    except ValueError as exc:
        raise ReviewMembershipError("The review media file is unavailable") from exc
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ReviewMembershipError("The review media file is unavailable")
    if review_file_matches(db, review.id, path) is False:
        raise ReviewMembershipError("The original Editarr review version is unavailable")
    return path
