"""Jellyfin scan creation."""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from scanner import api as root
from scanner.api import ScanRequest
from scanner.integrations.jellyfin import JellyfinError
from scanner.models import ScanQueueItem
from scanner.persistence.archive import names_archived_item
from scanner.persistence.repository.media_identity_store import observe_title
from scanner.persistence.repository.review_access import not_found
from scanner.persistence.repository.scan_jobs import ScanMedia
from scanner.repository import (
    create_scan,
    enqueue_scan,
    resolve_local_path,
    upsert_media_item,
)
from scanner.runtime.scan_version import ScanVersionChanged, file_generation, stage_scan_generation
from scanner.security.access.capability_ids import Capability as C

from .audio_tracks_api import require_audio_selection
from .scan_access import ScanCaller, scan_caller

router = APIRouter()


@router.post("/api/scans", response_model=ScanQueueItem, status_code=201)
def api_enqueue_scan(req: ScanRequest, caller: ScanCaller = Depends(scan_caller)):
    """Queue a scan from a Jellyfin item, never from a browser-supplied path."""
    db = caller.db
    credentials = root.get_jellyfin_credentials(db)
    if not credentials:
        raise HTTPException(409, "Configure Jellyfin before creating a scan")
    # Jellyfin would not find it either, and that miss must not read as a connection error.
    if names_archived_item(db, req.jellyfin_item_id):
        raise not_found()
    try:
        with root.JellyfinClient(*credentials) as client:
            item = client.item(req.jellyfin_item_id)
    except JellyfinError as exc:
        root.record_jellyfin_connection(db, str(exc))
        raise HTTPException(502, str(exc)) from exc
    if item.item_type in {"Series", "Season", "Folder", "BoxSet"}:
        raise HTTPException(400, "Select an individual Jellyfin video item to create a scan")
    selected_version = None
    if req.media_source_id:
        selected_version = next(
            (version for version in item.versions if version.id == req.media_source_id),
            None,
        )
        if selected_version is None:
            raise HTTPException(400, "The selected Jellyfin version is no longer available")
    source_path = selected_version.path if selected_version else item.path
    if not source_path:
        raise HTTPException(400, "The selected Jellyfin version does not expose a media path")
    try:
        resolution = resolve_local_path(db, source_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    local_path = Path(resolution.local_path)
    resolved = str(local_path.resolve())
    target = ScanMedia(credentials[0], item.id, item.library_id, resolved)
    caller.require_start(C.SCANS_START, target)
    if not local_path.is_file() or not os.access(local_path, os.R_OK):
        raise HTTPException(
            400,
            "Editarr cannot read this video on the scanner host; add a base path mapping if needed",
        )
    try:
        generation = file_generation(resolved)
    except ScanVersionChanged as exc:
        raise HTTPException(409, str(exc)) from exc
    require_audio_selection(req, resolved)
    source_id = selected_version.id if selected_version else None
    with caller.starting(C.SCANS_START, target):
        media_item = upsert_media_item(
            db,
            server_id=credentials[0],
            jellyfin_id=item.id,
            library_id=item.library_id,
            item_type=item.item_type,
            name=item.name,
            path=item.path or source_path,
            commit=False,
        )
        observe_title(db, media_item, item.media_identity)
        scan = create_scan(
            db,
            media_item=media_item,
            resolved_local_path=resolved,
            source_path=source_path,
            media_source_id=source_id,
            media_source_name=selected_version.name if selected_version else None,
            scene_name=selected_version.scene_name if selected_version else None,
            release_group=selected_version.release_group if selected_version else None,
            media_format=selected_version.media_format if selected_version else item.media_format,
            media_provider_id=item.media_provider_id,
            duration_seconds=item.duration_seconds,
            name=req.name,
            scan_audio=req.scan_audio,
            audio_model=req.audio_model,
            audio_filter=req.audio_filter,
            audio_selection=req.audio_selection,
            scan_video=req.scan_video,
            video_model=req.video_model,
            coarse_scan_interval=req.coarse_scan_interval,
            coarse_scan_enabled=req.coarse_scan_enabled,
            qwen_model=req.qwen_model,
            qwen_summaries_per_segment=req.qwen_summaries_per_segment,
            qwen_segment_join_gap_seconds=req.qwen_segment_join_gap_seconds,
            initiator=caller.initiator,
            commit=False,
            defer_video_hash=True,
        )
        try:
            stage_scan_generation(db, scan.id, resolved, generation)
        except ScanVersionChanged as exc:
            raise HTTPException(409, str(exc)) from exc
        queue_item = enqueue_scan(db, scan.id)
    root.record_jellyfin_connection(db, None)
    root._start_scan_queue_worker_if_needed()
    return caller.present(queue_item)
