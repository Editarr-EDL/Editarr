"""Authorized filter candidate discovery and association routes."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from scanner import api as root
from scanner.db import get_db
from scanner.models import (
    FilterCandidateAvailability,
    FilterCandidateSearchResult,
    ScanAssociationUpdate,
    ScanMatch,
    ScanMatchSearch,
)
from scanner.repository import (
    associate_scans_with_media_item,
    associated_scan_ids_for_media_item,
    find_filter_candidates,
    possible_candidate_scans,
    resolve_local_path,
    upsert_media_item,
)
from scanner.security.access.capability_ids import Capability

from . import catalog_files
from .catalog_access import CatalogViewer, catalog_viewer, not_found

router = APIRouter()


@router.post("/api/filter-candidates", response_model=FilterCandidateSearchResult)
def api_find_filter_candidates(
    search: ScanMatchSearch,
    db: Session = Depends(get_db),
    viewer: CatalogViewer = Depends(catalog_viewer(Capability.SCANS_FINDINGS_READ)),
):
    """Find reachable completed scans using only source-server media facts."""
    found = catalog_files.source_file(
        db,
        viewer,
        search.item_id,
        media_source_id=search.media_source_id,
        path=search.source_path,
    )
    try:
        resolution = resolve_local_path(db, found.path)
    except ValueError as exc:
        raise HTTPException(400, str(exc) if viewer.sees_paths() else "Unsupported path") from exc
    media_item = upsert_media_item(
        db,
        server_id=found.server_id,
        jellyfin_id=found.item_id,
        library_id=found.library_id,
        item_type=found.item_type,
        name=found.name,
        path=found.path,
    )
    associated_scan_ids = associated_scan_ids_for_media_item(db, media_item_id=media_item.id)
    resolved_local_path = str(Path(resolution.local_path).resolve())
    candidates = possible_candidate_scans(
        db, resolved_local_path=resolved_local_path, media_provider_id=found.media_provider_id
    )
    matches = []
    # Without candidate scans nothing can match, so the media file is never probed or hashed.
    if candidates:
        try:
            video_duration_seconds = root.get_video_duration(resolution.local_path)
        except (OSError, RuntimeError, ValueError):
            video_duration_seconds = found.duration_seconds
        matches = find_filter_candidates(
            db,
            resolved_local_path=resolved_local_path,
            duration_seconds=video_duration_seconds,
            scene_name=found.scene_name,
            release_group=found.release_group,
            media_format=found.media_format,
            media_provider_id=found.media_provider_id,
            source_path=found.path,
            candidates=candidates,
        )
    if not viewer.is_owner:
        stored = catalog_files.stored_scans(db, [scan.id for scan, _reason in matches])
        scan_ids = list(stored)
        rebuilt = catalog_files.stored_targets(db, viewer, [stored[key] for key in scan_ids])
        targets = dict(zip(scan_ids, rebuilt, strict=True))
        matches = viewer.visible(matches, lambda match: targets.get(match[0].id))
    return FilterCandidateSearchResult(
        media_item_id=media_item.id,
        matches=[
            ScanMatch(
                scan=(
                    scan if viewer.sees_paths() else scan.model_copy(update={"error_message": None})
                ),
                match_reason=reason,
                already_associated=scan.id in associated_scan_ids,
            )
            for scan, reason in matches
        ],
    )


@router.post("/api/filter-candidates/available", response_model=FilterCandidateAvailability)
def api_filter_candidates_available(
    search: ScanMatchSearch,
    db: Session = Depends(get_db),
    viewer: CatalogViewer = Depends(catalog_viewer(Capability.SCANS_FINDINGS_READ)),
):
    """Whether Recover Scans could find anything: a reachable completed scan of another
    file of the same work. Reads no media and writes nothing."""
    found = catalog_files.source_file(
        db,
        viewer,
        search.item_id,
        media_source_id=search.media_source_id,
        path=search.source_path,
    )
    try:
        resolution = resolve_local_path(db, found.path)
    except ValueError as exc:
        raise HTTPException(400, str(exc) if viewer.sees_paths() else "Unsupported path") from exc
    rows = possible_candidate_scans(
        db,
        resolved_local_path=str(Path(resolution.local_path).resolve()),
        media_provider_id=found.media_provider_id,
    )
    if rows and not viewer.is_owner:
        stored = catalog_files.stored_scans(db, [row.id for row in rows])
        scan_ids = list(stored)
        rebuilt = catalog_files.stored_targets(db, viewer, [stored[key] for key in scan_ids])
        targets = dict(zip(scan_ids, rebuilt, strict=True))
        rows = viewer.visible(rows, lambda row: targets.get(row.id))
    return FilterCandidateAvailability(available=bool(rows))


@router.post("/api/filter-candidates/associations", status_code=204)
def api_associate_filter_candidates(
    update: ScanAssociationUpdate,
    db: Session = Depends(get_db),
    viewer: CatalogViewer = Depends(catalog_viewer(Capability.MEDIA_IDENTITY_EDIT)),
):
    """Associate only scans and media independently authorized from their source records."""
    if not viewer.is_owner:
        media = catalog_files.stored_media_item(db, update.media_item_id)
        scans = catalog_files.stored_scans(db, update.scan_ids)
        rows = [media, *(scans.get(scan_id) for scan_id in update.scan_ids)]
        if any(row is None for row in rows):
            raise not_found()
        for target in catalog_files.stored_targets(db, viewer, rows):
            viewer.require(target)
    try:
        associate_scans_with_media_item(
            db, media_item_id=update.media_item_id, scan_ids=update.scan_ids
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(status_code=204)
