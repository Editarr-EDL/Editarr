"""Repository operations for export attribution and sidecar state."""

import os
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from scanner.db import MediaItemExportRow, MediaItemRow, SavedReviewRow
from scanner.models import (
    ExportFormat,
    ExportScanReference,
    ExportStatus,
    ExportSummary,
    ReviewTarget,
    SavedReviewSummary,
)
from scanner.persistence.rows.db_policies import ExportScope

from .repository_paths import resolve_local_path
from .repository_rows import _community_provenance, _utc
from .review_repairs import repair_summary, summarize_repairs

# Legacy adjacent sidecars remain separate from caller-scoped canonical discovery.
_LEGACY = MediaItemExportRow.scope == ExportScope.LEGACY


def _review_target(
    row: SavedReviewRow, *, source: str | None = None, item_id: str | None = None
) -> ReviewTarget:
    """Build the public target reference from a saved review row."""
    return ReviewTarget(
        source=source or "jellyfin",
        item_id=item_id or row.media_item.jellyfin_id,
        media_source_id=row.media_source_id or None,
    )


def target_export_rows(
    db: Session, media_item_id: str, media_source_id: str
) -> list[MediaItemExportRow]:
    """Load the legacy sidecar export rows shared by every review of one target."""
    return (
        db.query(MediaItemExportRow)
        .filter(
            MediaItemExportRow.media_item_id == media_item_id,
            MediaItemExportRow.media_source_id == media_source_id,
            _LEGACY,
        )
        .all()
    )


def _review_summary(
    db: Session,
    row: SavedReviewRow,
    *,
    source: str | None = None,
    item_id: str | None = None,
    export_rows: list[MediaItemExportRow] | None = None,
    repair_records: list | None = None,
) -> SavedReviewSummary:
    """Return a saved review summary with the current export state.

    List callers may pass the target's ``export_rows`` and the review's
    ``repair_records`` preloaded; otherwise both are queried.
    """
    if export_rows is None:
        export_rows = target_export_rows(db, row.media_item_id, row.media_source_id)
    exports_by_format = {export.format: export for export in export_rows}
    summaries = {
        export_format: _export_summary(exports_by_format.get(export_format), export_format, row)
        for export_format in ExportFormat
    }
    return SavedReviewSummary(
        id=row.id,
        media_item_id=row.media_item_id,
        target=_review_target(row, source=source, item_id=item_id),
        name=row.name,
        revision=row.revision,
        content_revision=row.content_revision,
        edl_content_revision=row.edl_content_revision,
        mcf_content_revision=row.mcf_content_revision,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
        repair=(
            repair_summary(db, row.id)
            if repair_records is None
            else summarize_repairs(repair_records)
        ),
        exports=ExportStatus(exports=summaries, legacy_exports=summaries),
    )


def _export_summary(
    export: MediaItemExportRow | None,
    export_format: ExportFormat,
    review: SavedReviewRow | None,
) -> ExportSummary:
    """Describe one sidecar export and its contributing scan references."""
    attributed = export is not None and review is not None and export.review_id == review.id
    current_revision = (
        (
            review.edl_content_revision
            if export_format == ExportFormat.EDL
            else review.mcf_content_revision
        )
        if review is not None
        else None
    )
    return ExportSummary(
        format=export_format,
        present=export is not None,
        exported_at=_utc(export.exported_at) if export is not None else None,
        review_id=export.review_id if export is not None else None,
        exported_format_revision=(export.exported_format_revision if export is not None else None),
        current=attributed and export.exported_format_revision == current_revision,
        outdated=attributed and export.exported_format_revision != current_revision,
        exported_scans=(
            [
                ExportScanReference(
                    id=link.scan.id,
                    scan_id=link.scan.scan_number,
                    role=link.role,
                    name=link.scan.name,
                    origin=link.scan.origin,
                    community_provenance=_community_provenance(link.scan),
                )
                for link in export.scans
            ]
            if export is not None
            else []
        ),
    )


def _export_status_from_rows(db: Session, rows: list[MediaItemExportRow]) -> ExportStatus:
    """Combine format rows into one EDL and MCF status payload."""
    rows_by_format = {row.format: row for row in rows}
    summaries: dict[ExportFormat, ExportSummary] = {}
    for export_format in ExportFormat:
        export = rows_by_format.get(export_format)
        review = export.review if export is not None else None
        summary = _export_summary(export, export_format, review)
        if review is not None:
            summary = summary.model_copy(update={"exported_review": _review_summary(db, review)})
        summaries[export_format] = summary
    return ExportStatus(exports=summaries, legacy_exports=summaries)


def library_item_export_statuses(
    db: Session, *, server_id: str, jellyfin_item_ids: list[str]
) -> dict[str, ExportStatus]:
    """Return the newest export of each format for requested catalog items."""
    if not jellyfin_item_ids:
        return {}
    rows = (
        db.query(MediaItemExportRow, MediaItemRow.jellyfin_id)
        .join(MediaItemRow, MediaItemExportRow.media_item_id == MediaItemRow.id)
        .filter(
            MediaItemRow.server_id == server_id,
            MediaItemRow.jellyfin_id.in_(jellyfin_item_ids),
            _LEGACY,
        )
        .all()
    )
    selected: dict[str, dict[ExportFormat, MediaItemExportRow]] = {}
    for export, jellyfin_id in rows:
        current = selected.setdefault(jellyfin_id, {}).get(export.format)
        if current is None or export.exported_at > current.exported_at:
            selected[jellyfin_id][export.format] = export
    return {
        item_id: _export_status_from_rows(db, list(selected.get(item_id, {}).values()))
        for item_id in jellyfin_item_ids
    }


def library_item_version_export_statuses(
    db: Session, *, server_id: str, jellyfin_item_id: str
) -> dict[str, ExportStatus]:
    """Return format-keyed export state for every persisted physical version."""
    return library_item_version_export_statuses_for_items(
        db, server_id=server_id, jellyfin_item_ids=[jellyfin_item_id]
    ).get(jellyfin_item_id, {})


def library_item_version_export_statuses_for_items(
    db: Session, *, server_id: str, jellyfin_item_ids: list[str]
) -> dict[str, dict[str, ExportStatus]]:
    """Batch export statuses for media item and source version pairs."""
    if not jellyfin_item_ids:
        return {}
    rows = (
        db.query(MediaItemExportRow, MediaItemRow.jellyfin_id)
        .join(MediaItemRow, MediaItemRow.id == MediaItemExportRow.media_item_id)
        .filter(
            MediaItemRow.server_id == server_id,
            MediaItemRow.jellyfin_id.in_(jellyfin_item_ids),
            _LEGACY,
        )
        .all()
    )
    grouped: dict[str, dict[str, list[MediaItemExportRow]]] = {}
    for row, jellyfin_item_id in rows:
        grouped.setdefault(jellyfin_item_id, {}).setdefault(row.media_source_id, []).append(row)
    return {
        item_id: {
            version_id: _export_status_from_rows(db, version_rows)
            for version_id, version_rows in grouped.get(item_id, {}).items()
        }
        for item_id in jellyfin_item_ids
    }


def media_item_export_status(
    db: Session,
    *,
    server_id: str,
    jellyfin_item_id: str,
    media_source_id: str = "",
) -> ExportStatus:
    """Read both sidecar statuses for a media item and source version."""
    rows = (
        db.query(MediaItemExportRow)
        .join(MediaItemRow, MediaItemRow.id == MediaItemExportRow.media_item_id)
        .filter(
            MediaItemRow.server_id == server_id,
            MediaItemRow.jellyfin_id == jellyfin_item_id,
            MediaItemExportRow.media_source_id == media_source_id,
            _LEGACY,
        )
        .all()
    )
    return _export_status_from_rows(db, rows)


def delete_library_item_export(
    db: Session,
    *,
    server_id: str,
    jellyfin_item_id: str,
    media_source_id: str | None,
    export_format: ExportFormat,
) -> bool:
    """Remove one format sidecar and its matching persisted export state."""
    row = (
        db.query(MediaItemExportRow)
        .join(MediaItemRow, MediaItemRow.id == MediaItemExportRow.media_item_id)
        .filter(
            MediaItemRow.server_id == server_id,
            MediaItemRow.jellyfin_id == jellyfin_item_id,
            MediaItemExportRow.media_source_id == (media_source_id or ""),
            MediaItemExportRow.format == export_format,
            _LEGACY,
        )
        .one_or_none()
    )
    if row is None:
        return False
    scan_link = row.scans[0] if row.scans else None
    media_path = (
        scan_link.scan.resolved_local_path
        if scan_link is not None
        else (
            resolve_local_path(db, row.media_item.path).local_path
            if not row.media_source_id and row.media_item.path
            else None
        )
    )
    if not media_path:
        raise ValueError("Active export provenance is incomplete")
    destination = Path(media_path).with_suffix(f".{export_format.value}")
    backup_path: Path | None = None
    try:
        if destination.exists():
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".delete",
                delete=False,
            ) as backup_file:
                backup_path = Path(backup_file.name)
            os.replace(destination, backup_path)
    except OSError as exc:
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)
        raise ValueError(f"Unable to delete {export_format.value.upper()} export") from exc
    try:
        db.delete(row)
        db.commit()
    except Exception as exc:
        db.rollback()
        if backup_path is not None and backup_path.exists():
            try:
                os.replace(backup_path, destination)
            except OSError as restore_exc:
                raise ValueError(
                    f"Unable to restore {export_format.value.upper()} after delete failure"
                ) from restore_exc
        raise ValueError(f"Unable to delete {export_format.value.upper()} export") from exc
    if backup_path is not None:
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def delete_library_item_edl(
    db: Session,
    *,
    server_id: str,
    jellyfin_item_id: str,
    media_source_id: str | None,
) -> bool:
    """Delete the active EDL sidecar and its persisted export state."""
    return delete_library_item_export(
        db,
        server_id=server_id,
        jellyfin_item_id=jellyfin_item_id,
        media_source_id=media_source_id,
        export_format=ExportFormat.EDL,
    )
