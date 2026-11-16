"""Protected file-bound audio inventory and selection endpoints."""

import os
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.modeling.model_audio_tracks import (
    AudioInventory,
    AudioSelection,
    AudioSelectionRequest,
)
from scanner.persistence.repository.repository_audio_tracks import (
    remember_audio_selection,
    remembered_audio_selection,
)
from scanner.pipeline.audio_tracks import AudioSelectionError, probe_audio_tracks
from scanner.repository import resolve_local_path
from scanner.security.access.capability_ids import Capability
from scanner.server_paths import UnresolvablePath

from .catalog_access import CatalogViewer, catalog_viewer
from .catalog_files import source_file

router = APIRouter()
_UNAVAILABLE = "Audio inventory unavailable; refresh the selected file"
_tracks = catalog_viewer(Capability.SCANS_START)


def _file(local_path: str) -> Path:
    path = Path(local_path)
    try:
        if not path.is_absolute():
            raise ValueError("A mapped local path must be absolute")
        path = path.resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, "The selected file is unavailable") from exc
    if not path.is_file() or not os.access(path, os.R_OK):
        raise HTTPException(400, "The selected file is not readable")
    return path


def _track_file(
    db: Session, viewer: CatalogViewer, item_id: str, media_source_id: str | None
) -> Path:
    """The authorized local file of a Jellyfin item version."""
    found = source_file(db, viewer, item_id, media_source_id=media_source_id)
    try:
        return _file(resolve_local_path(db, found.path).local_path)
    except UnresolvablePath as exc:
        raise HTTPException(409, _UNAVAILABLE) from exc


def require_audio_selection(request, path: str) -> None:
    """Validate an explicit input before queue admission; video-only needs none."""
    if not request.scan_audio:
        return
    if request.audio_selection is None:
        raise HTTPException(409, "Choose an audio track before starting the scan")
    try:
        probe_audio_tracks(path).revalidate(request.audio_selection.selection())
    except (OSError, ValueError) as exc:
        raise HTTPException(
            409, "Audio selection is stale; refresh and choose a track again"
        ) from exc


@router.get("/api/media/audio-tracks", response_model=AudioInventory)
def get_audio_tracks(
    item_id: str = Query(min_length=1, max_length=256),
    media_source_id: str | None = Query(None, max_length=256),
    db: Session = Depends(get_db),
    viewer: CatalogViewer = Depends(_tracks),
):
    """Return observed tracks and only the approved unambiguous preselection."""
    path = _track_file(db, viewer, item_id, media_source_id)
    try:
        inventory = probe_audio_tracks(path)
        remembered = remembered_audio_selection(db, path)
        stale = False
        selection = (
            AudioSelection.from_selection(inventory.select())
            if inventory.preselected_stream_index is not None
            else None
        )
        if remembered is not None:
            try:
                inventory.revalidate(remembered.selection())
                selection = remembered
            except AudioSelectionError:
                selection = None
                stale = True
        return AudioInventory(
            file_version=inventory.file_version,
            tracks=[asdict(track) for track in inventory.tracks],
            selection=selection,
            selection_stale=stale,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(409, _UNAVAILABLE) from exc


@router.post("/api/media/audio-tracks/selection", response_model=AudioSelection)
def select_audio_track(
    request: AudioSelectionRequest,
    db: Session = Depends(get_db),
    viewer: CatalogViewer = Depends(_tracks),
):
    """Validate the exact file/track snapshot before accepting a selection."""
    path = _track_file(db, viewer, request.item_id, request.media_source_id)
    try:
        inventory = probe_audio_tracks(path)
        inventory.revalidate(request.selection.selection())
        remember_audio_selection(db, path, request.selection)
        return request.selection
    except AudioSelectionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(409, _UNAVAILABLE) from exc
