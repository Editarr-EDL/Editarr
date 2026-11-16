"""Atomic sidecar publishing shared by EDL and MCF review exports."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from scanner.db import (
    MediaItemExportRow,
    MediaItemExportScanRow,
    ReviewScanMembershipRow,
    SavedReviewRow,
)
from scanner.models import ExportFormat
from scanner.persistence.rows.db_policies import ExportScope

from .activity_ownership import activity_owner
from .export_recovery import (
    ExportRecoveryRequired,
    committed_export_content,
    recoverable_sidecar,
)
from .review_policy import PreparedReviewPolicy, ReviewPolicyConflict, assert_current_policy


def publish_review_sidecar(
    db: Session,
    *,
    review: SavedReviewRow,
    memberships: list[ReviewScanMembershipRow],
    destination: Path,
    content: bytes,
    export_format: ExportFormat,
    content_revision: int,
    policy: PreparedReviewPolicy,
    pass_through_errors: tuple[type[Exception], ...] = (),
    publication=None,
) -> MediaItemExportRow:
    """Replace a sidecar and its export record as one recoverable operation.

    Write and sync a sibling temporary file, retain a copy of the previous
    sidecar for rollback, then update the database. Keep the backup if restoring
    the old destination itself fails. The destination is the adjacent sidecar, so
    the record is a legacy-scope export.
    """
    try:
        assert_current_policy(db, review.id, policy)
        actor = activity_owner(db)
        export = (
            publication.row
            if publication is not None
            else (
                db.query(MediaItemExportRow)
                .filter(
                    MediaItemExportRow.media_item_id == review.media_item_id,
                    MediaItemExportRow.media_source_id == review.media_source_id,
                    MediaItemExportRow.format == export_format,
                    MediaItemExportRow.scope == ExportScope.LEGACY,
                )
                .one_or_none()
            )
        )
        if export is None:
            export = MediaItemExportRow(
                media_item_id=review.media_item_id,
                media_source_id=review.media_source_id,
                format=export_format,
                scope=publication.scope if publication is not None else ExportScope.LEGACY,
                target_user_id=publication.target_user_id if publication is not None else None,
                target_export_id=(
                    publication.target.value
                    if publication is not None and publication.target is not None
                    else None
                ),
            )
            db.add(export)
            if publication is None:
                db.flush()
        export.exported_at = datetime.now(timezone.utc)
        export.review_id = review.id
        export.exported_format_revision = content_revision
        export.actor_id = actor.user_id if actor is not None else None
        export.policy_owner_id = review.owner_id
        export.original_author_id = review.original_author_id
        export.policy_revision = policy.policy_revision
        if publication is not None:
            publication.stage(db, export, content)
        stage_export_scans(db, export.id, memberships)
        assert_current_policy(db, review.id, policy)
        export_id = export.id
        with recoverable_sidecar(
            destination,
            content,
            journaled=publication is not None,
            committed_content=(
                (lambda: committed_export_content(db, export_id))
                if publication is not None
                else None
            ),
        ):
            db.commit()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, (ReviewPolicyConflict, ExportRecoveryRequired, *pass_through_errors)):
            raise
        raise ValueError(f"Unable to export {export_format.value.upper()}") from exc
    db.refresh(export)
    return export


def stage_export_scans(db, export_id, memberships):
    """Keep the canonical provenance links aligned with the published review snapshot."""
    db.query(MediaItemExportScanRow).filter(
        MediaItemExportScanRow.media_item_export_id == export_id
    ).delete()
    for membership in memberships:
        db.add(
            MediaItemExportScanRow(
                media_item_export_id=export_id,
                scan_id=membership.scan_id,
                role=membership.role,
            )
        )
