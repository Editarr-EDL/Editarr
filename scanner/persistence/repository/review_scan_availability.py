"""Whether a scan belongs to a review's physical media version, and its credits media."""

import os
from pathlib import Path

from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import ScanMediaItemAssociationRow, ScanRow
from scanner.repository import CreditsRouteError

from .community_import_targets import verified_target
from .version_reuse import bound_scan_file_matches, source_pending_reuse


def _scan_is_available(
    db: Session, scan: ScanRow, *, media_item_id: str, media_source_id: str
) -> bool:
    """Check whether a completed scan belongs to the requested media version."""
    if source_pending_reuse(scan):
        return False
    if scan.community_import is not None and scan.community_import.artifact_bytes:
        return (
            scan.status == "complete"
            and verified_target(db, scan, media_item_id, media_source_id) is not None
        )
    return (
        scan.status == "complete"
        and scan.media_source_id == (media_source_id or None)
        and (
            scan.media_item_id == media_item_id
            or db.query(ScanMediaItemAssociationRow)
            .filter(
                ScanMediaItemAssociationRow.scan_id == scan.id,
                ScanMediaItemAssociationRow.media_item_id == media_item_id,
            )
            .first()
            is not None
        )
    )


def _resolved_scan_path(scan: ScanRow) -> Path:
    return Path(scan.resolved_local_path).resolve()


def _compute_video_hash(path: str | Path) -> str | None:
    try:
        return root.compute_video_md5(str(path))
    except OSError:
        return None


def _scan_matches_physical_version(
    scan: ScanRow, target_path: Path, *, require_hash: bool = False
) -> bool:
    """Check whether a scan still belongs to the requested physical media version."""
    approval = bound_scan_file_matches(scan, target_path)
    if approval is not None:
        return approval
    if _resolved_scan_path(scan) != target_path.resolve():
        return False
    if scan.video_hash is None:
        return not require_hash
    try:
        return root.compute_video_md5(str(target_path)) == scan.video_hash
    except OSError:
        return False


def _validated_credits_media_path(db: Session, scan: ScanRow) -> Path:
    """Resolve readable credits media and verify its physical file hash."""
    if not scan.media_item.path:
        raise CreditsRouteError("credits_media_unavailable", "Credits media is unavailable", 503)
    try:
        media_path = Path(root.resolve_local_path(db, scan.media_item.path).local_path).resolve(
            strict=True
        )
    except (OSError, ValueError) as exc:
        raise CreditsRouteError(
            "credits_media_unavailable", "Credits media is unavailable", 503
        ) from exc
    if not media_path.is_file() or not os.access(media_path, os.R_OK):
        raise CreditsRouteError("credits_media_unavailable", "Credits media is unavailable", 503)
    if not _scan_matches_physical_version(scan, media_path, require_hash=True):
        raise CreditsRouteError(
            "credits_target_mismatch",
            "Credits detection does not match the physical media file",
            409,
        )
    return media_path
