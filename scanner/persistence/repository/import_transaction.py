"""Shared row staging and transaction boundaries for imported scan evidence."""

import uuid
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import MediaItemRow, SavedReviewRow, ScanMcfDocumentRow, ScanRow
from scanner.models import ScanOrigin

from .activity_ownership import new_review_owner_columns, new_scan_initiator_columns


@contextmanager
def rollback_on_import_error(db: Session):
    """Roll back failures inside the caller's existing import transaction boundary."""
    try:
        yield
    except Exception:
        db.rollback()
        raise


def stage_imported_scan(
    db: Session,
    *,
    media_item: MediaItemRow,
    scan_number: int,
    resolved_local_path: str,
    media_source_id: str | None,
    source_name: str,
    review_name: str,
    duration_seconds: float | None,
    has_audio: bool,
    has_video: bool,
    progress_message: str,
    imported_at: datetime,
    mcf_document: ScanMcfDocumentRow | None = None,
) -> ScanRow:
    """Flush an imported scan without committing; callers stage its evidence next."""
    scan = ScanRow(
        id=str(uuid.uuid4()),
        media_item_id=media_item.id,
        scan_number=scan_number,
        name=review_name,
        origin=ScanOrigin.IMPORTED.value,
        import_source_name=source_name,
        imported_at=imported_at,
        source_path=resolved_local_path,
        media_source_id=media_source_id,
        media_source_name=source_name,
        resolved_local_path=resolved_local_path,
        video_hash=root._compute_video_hash(resolved_local_path),
        duration_seconds=duration_seconds,
        status="complete",
        scan_audio=has_audio,
        audio_model="imported" if has_audio else None,
        scan_video=has_video,
        video_model="imported" if has_video else None,
        checkpoint_stage="imported",
        progress_percent=100.0,
        progress_stage="imported",
        progress_message=progress_message,
        created_at=imported_at,
        updated_at=imported_at,
        **new_scan_initiator_columns(db),
    )
    if mcf_document is not None:
        scan.mcf_document = mcf_document
    db.add(scan)
    db.flush()
    return scan


def stage_imported_review(
    db: Session,
    *,
    media_item: MediaItemRow,
    media_source_id: str | None,
    review_name: str,
    imported_at: datetime,
) -> SavedReviewRow:
    """Flush a new isolated review before attaching memberships and editable copies."""
    review = SavedReviewRow(
        id=str(uuid.uuid4()),
        media_item_id=media_item.id,
        media_source_id=media_source_id or "",
        name=review_name,
        created_at=imported_at,
        updated_at=imported_at,
        **new_review_owner_columns(db),
    )
    db.add(review)
    db.flush()
    return review


def refresh_imported_rows(db: Session, scan: ScanRow, review: SavedReviewRow) -> None:
    """Refresh the committed rows before building the existing public result DTOs."""
    db.refresh(scan)
    db.refresh(review)
