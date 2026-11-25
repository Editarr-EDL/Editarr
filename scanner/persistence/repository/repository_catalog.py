"""Repository operations for media items, scan history, and findings."""

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from scanner.db import (
    MediaItemRow,
    ScanFindingRow,
    ScanMediaItemAssociationRow,
    ScanQueueRow,
    ScanRow,
)
from scanner.models import (
    EditAction,
    FindingCreate,
    Scan,
    ScanFindings,
    ScanQueueItem,
    normalize_scan_name,
)
from scanner.pipeline.cache import compute_video_md5

from .community_import_targets import scan_pending_targets_by_scan, scan_target_approvals_by_scan
from .repository_rows import (
    _scan_finding_from_row,
    _scan_from_row,
    _scan_queue_from_row,
    scan_list_loading,
)
from .scan_verification_coverage import verification_coverage_by_scan


def possible_candidate_scans(
    db: Session, *, resolved_local_path: str, media_provider_id: str | None
) -> list[ScanRow]:
    """Completed scans of other files of the same work (TMDB/TVDB provider ID), newest first.

    These are every scan `find_filter_candidates` may match; reading them needs no hashing,
    so Recover Scans uses them to tell whether anything could be recovered.
    """
    if not media_provider_id:
        return []
    return (
        db.query(ScanRow)
        .join(MediaItemRow)
        .filter(
            ScanRow.status == "complete",
            ScanRow.resolved_local_path != resolved_local_path,
            ScanRow.media_provider_id == media_provider_id,
        )
        .order_by(ScanRow.updated_at.desc(), ScanRow.created_at.desc())
        .all()
    )


def find_filter_candidates(
    db: Session,
    *,
    resolved_local_path: str,
    duration_seconds: float | None,
    scene_name: str | None,
    release_group: str | None,
    media_format: str | None,
    media_provider_id: str | None,
    source_path: str,
) -> list[tuple[Scan, str]]:
    """Find completed scans of likely matching, but not yet linked, media files."""
    rows = possible_candidate_scans(
        db, resolved_local_path=resolved_local_path, media_provider_id=media_provider_id
    )
    normalized_scene_name = _normalized_media_name(scene_name) if scene_name else None
    normalized_release_group = _normalized_media_name(release_group) if release_group else None
    normalized_media_format = _normalized_media_name(media_format) if media_format else None
    target_file_name = _media_file_name(source_path)
    matches: dict[str, tuple[Scan, str]] = {}
    unmatched_rows: list[ScanRow] = []
    for row in rows:
        scene_release_matches = (
            normalized_scene_name is not None
            and normalized_release_group is not None
            and _normalized_media_name(row.scene_name) == normalized_scene_name
            and _normalized_media_name(row.release_group) == normalized_release_group
        )
        if scene_release_matches:
            matches[row.id] = (_scan_from_row(row), "scene_release")
        elif (
            target_file_name is not None
            and _media_file_name(row.source_path) == target_file_name
            and _durations_match(row.duration_seconds, duration_seconds)
        ):
            matches[row.id] = (_scan_from_row(row), "file_name")
        elif (
            normalized_media_format is not None
            and _normalized_media_name(row.media_format) == normalized_media_format
            and _durations_match(row.duration_seconds, duration_seconds)
        ):
            matches[row.id] = (_scan_from_row(row), "format")
        else:
            unmatched_rows.append(row)

    target_path = Path(resolved_local_path)
    try:
        target_hash = compute_video_md5(str(target_path)) if target_path.is_file() else None
    except OSError:
        target_hash = None
    if target_hash is not None:
        for row in unmatched_rows:
            # Reuse a stored hash when available; otherwise read the candidate file.
            candidate_hash = row.video_hash
            if candidate_hash is None:
                path = Path(row.resolved_local_path)
                try:
                    candidate_hash = compute_video_md5(str(path)) if path.is_file() else None
                except OSError:
                    candidate_hash = None
            if candidate_hash == target_hash and _durations_match(
                row.duration_seconds, duration_seconds
            ):
                matches[row.id] = (_scan_from_row(row), "quick_hash")
    return list(matches.values())


def upsert_media_item(
    db: Session,
    *,
    server_id: str,
    jellyfin_id: str,
    library_id: str | None,
    item_type: str,
    name: str,
    path: str | None,
    commit: bool = True,
) -> MediaItemRow:
    """Create or refresh a catalog row identified by ``server_id`` and ``jellyfin_id``."""
    row = (
        db.query(MediaItemRow)
        .filter(MediaItemRow.server_id == server_id, MediaItemRow.jellyfin_id == jellyfin_id)
        .first()
    )
    if row is None:
        row = MediaItemRow(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{server_id}|{jellyfin_id}")),
            server_id=server_id,
            jellyfin_id=jellyfin_id,
            library_id=library_id,
            item_type=item_type,
            name=name,
            path=path,
        )
        db.add(row)
    else:
        row.library_id = library_id
        row.item_type = item_type
        row.name = name
        row.path = path
        row.updated_at = datetime.now(timezone.utc)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def rename_scan(db: Session, scan_id: str, name: str | None) -> Scan | None:
    """Change display metadata without touching worker or finding revisions."""
    name = normalize_scan_name(name)
    row = db.get(ScanRow, scan_id)
    if row is None:
        return None
    if row.name != name:
        row.name = name
        db.commit()
        db.refresh(row)
    finding_count = db.query(ScanFindingRow).filter(ScanFindingRow.scan_id == scan_id).count()
    return _scan_from_row(row, finding_count=finding_count)


def get_scan(db: Session, scan_id: str) -> Scan | None:
    row = db.get(ScanRow, scan_id)
    if row is None:
        return None
    finding_count = db.query(ScanFindingRow).filter(ScanFindingRow.scan_id == scan_id).count()
    return _scan_from_row(row, finding_count=finding_count)


def enqueue_scan(db: Session, scan_id: str) -> ScanQueueItem:
    """Persist one scan and its queue item for later worker processing."""
    max_sort = db.query(ScanQueueRow.sort_order).order_by(ScanQueueRow.sort_order.desc()).first()
    row = ScanQueueRow(
        scan_id=scan_id,
        sort_order=(max_sort[0] if max_sort else -1) + 1,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _scan_queue_from_row(row)


def list_scan_queue(db: Session) -> list[ScanQueueItem]:
    rows = db.query(ScanQueueRow).order_by(ScanQueueRow.sort_order, ScanQueueRow.id).all()
    return [_scan_queue_from_row(row) for row in rows]


def _finding_counts(db: Session, scans: list[ScanRow]) -> dict[str, int]:
    if not scans:
        return {}
    return dict(
        db.query(ScanFindingRow.scan_id, func.count(ScanFindingRow.id))
        .filter(ScanFindingRow.scan_id.in_([scan.id for scan in scans]))
        .group_by(ScanFindingRow.scan_id)
        .all()
    )


def list_scan_history(db: Session) -> list[Scan]:
    """Return persisted scans in history order with their finding counts."""
    rows = (
        db.query(ScanRow)
        .options(*scan_list_loading())
        .filter(ScanRow.status.in_(("complete", "error", "failed", "cancelled")))
        .order_by(ScanRow.updated_at.desc(), ScanRow.created_at.desc())
        .all()
    )
    approvals = scan_target_approvals_by_scan(db, rows)
    pending = scan_pending_targets_by_scan(db, rows)
    coverage = verification_coverage_by_scan(db, rows)
    finding_counts = _finding_counts(db, rows)
    return [
        _scan_from_row(
            row,
            finding_count=finding_counts.get(row.id, 0),
            community_approvals=approvals[row.id],
            community_pending=pending.get(row.id, []),
            verification_coverage=coverage,
        )
        for row in rows
    ]


def list_scans_for_library_item(
    db: Session,
    *,
    jellyfin_item_id: str,
    server_id: str | None = None,
    associated_only: bool = False,
) -> list[Scan]:
    """Find locally stored scans and explicit links for a Jellyfin item."""
    items_query = db.query(MediaItemRow.id).filter(MediaItemRow.jellyfin_id == jellyfin_item_id)
    if server_id:
        items_query = items_query.filter(MediaItemRow.server_id == server_id)
    media_item_ids = [row[0] for row in items_query.all()]
    if not media_item_ids:
        return []
    linked = ScanMediaItemAssociationRow.media_item_id.in_(media_item_ids)
    owned = ScanRow.media_item_id.in_(media_item_ids)
    rows = (
        db.query(ScanRow)
        .options(*scan_list_loading())
        .outerjoin(ScanMediaItemAssociationRow)
        .filter(linked & ~owned if associated_only else or_(owned, linked))
        .distinct()
        .order_by(ScanRow.updated_at.desc(), ScanRow.created_at.desc())
        .all()
    )
    approvals = scan_target_approvals_by_scan(db, rows)
    pending = scan_pending_targets_by_scan(db, rows)
    coverage = verification_coverage_by_scan(db, rows)
    finding_counts = _finding_counts(db, rows)
    return [
        _scan_from_row(
            row,
            finding_count=finding_counts.get(row.id, 0),
            community_approvals=approvals[row.id],
            community_pending=pending.get(row.id, []),
            verification_coverage=coverage,
        )
        for row in rows
    ]


def associate_scans_with_media_item(
    db: Session, *, media_item_id: str, scan_ids: list[str]
) -> None:
    """Link completed scans for a matching physical path to ``media_item_id``."""
    if not db.get(MediaItemRow, media_item_id):
        raise ValueError("Media item no longer exists")
    requested_ids = set(scan_ids)
    found_scan_ids = {
        row[0] for row in db.query(ScanRow.id).filter(ScanRow.id.in_(requested_ids)).all()
    }
    if found_scan_ids != requested_ids:
        raise ValueError("One or more selected scans no longer exist")
    existing_scan_ids = {
        row[0]
        for row in db.query(ScanMediaItemAssociationRow.scan_id)
        .filter(
            ScanMediaItemAssociationRow.media_item_id == media_item_id,
            ScanMediaItemAssociationRow.scan_id.in_(requested_ids),
        )
        .all()
    }
    db.add_all(
        [
            ScanMediaItemAssociationRow(scan_id=scan_id, media_item_id=media_item_id)
            for scan_id in requested_ids - existing_scan_ids
        ]
    )
    db.commit()


def associated_scan_ids_for_media_item(db: Session, *, media_item_id: str) -> set[str]:
    """Return identifiers of scans explicitly linked to ``media_item_id``."""
    explicit_association_ids = {
        row[0]
        for row in db.query(ScanMediaItemAssociationRow.scan_id)
        .filter(ScanMediaItemAssociationRow.media_item_id == media_item_id)
        .all()
    }
    owned_scan_ids = {
        row[0] for row in db.query(ScanRow.id).filter(ScanRow.media_item_id == media_item_id).all()
    }
    return explicit_association_ids | owned_scan_ids


def create_scan_findings_batch(db: Session, scan_id: str, findings: list[FindingCreate]) -> None:
    """Store scanner findings for ``scan_id`` in one transaction."""
    rows = [
        ScanFindingRow(
            scan_id=scan_id,
            category=finding.category,
            subcategory=finding.subcategory,
            severity=finding.severity,
            mcf_channel=finding.mcf_channel,
            mcf_comment=finding.mcf_comment,
            start_seconds=finding.start_seconds,
            end_seconds=finding.end_seconds,
            confidence=finding.confidence,
            label=finding.label,
            details=finding.details,
            source=finding.source,
            selected=finding.selected,
            edit_action=(finding.edit_action or _default_action_for_source(finding.source)).value,
            group_key=finding.group_key or _default_group_key(finding),
        )
        for finding in findings
    ]
    db.add_all(rows)
    db.commit()


def get_scan_findings(db: Session, scan_id: str) -> ScanFindings | None:
    """Load findings and scan metadata for ``scan_id``, if it exists."""
    scan = db.get(ScanRow, scan_id)
    if not scan:
        return None
    rows = (
        db.query(ScanFindingRow)
        .filter(ScanFindingRow.scan_id == scan_id)
        .order_by(ScanFindingRow.start_seconds)
        .all()
    )
    return ScanFindings(
        revision=scan.selection_revision,
        findings=[
            _scan_finding_from_row(row, duration_seconds=scan.duration_seconds) for row in rows
        ],
    )


def _audio_group_key(label: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", label.lower())
    return normalized or "unidentified-profanity"


def _default_action_for_source(source: str) -> EditAction:
    return EditAction.MUTE if source == "audio" else EditAction.SKIP


def _default_group_key(finding: FindingCreate) -> str | None:
    if finding.source != "audio":
        return finding.category.value
    label = finding.label or "unidentified-profanity"
    return _audio_group_key(label)


def _normalized_media_name(value: str | None) -> str:
    return "".join(character for character in (value or "").casefold() if character.isalnum())


def _media_file_name(source_path: str | None) -> str | None:
    if not source_path:
        return None
    path = PureWindowsPath(source_path) if "\\" in source_path else PurePosixPath(source_path)
    return path.name.casefold() or None


def _media_format_from_path(video_path: str) -> str | None:
    suffix = Path(video_path).suffix.removeprefix(".").upper()
    return suffix or None


def _durations_match(left: float | None, right: float | None) -> bool:
    return left is not None and right is not None and abs(left - right) <= 2.0
