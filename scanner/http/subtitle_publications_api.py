"""List the shared filtered subtitle files for one authorized Jellyfin version."""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from scanner.modeling.model_subtitles import PublishedSubtitle
from scanner.runtime.subtitle_publication_names import subtitle_publisher_id
from scanner.runtime.subtitle_sidecars import discover_publications, media_slot
from scanner.security.access.capability_ids import Capability

from .catalog_access import CatalogViewer, catalog_viewer

router = APIRouter()
_viewer = catalog_viewer(Capability.MEDIA_BROWSE)


class SubtitlePublications(BaseModel):
    published_subtitles: list[PublishedSubtitle]


@router.get("/api/media/subtitles/publications", response_model=SubtitlePublications)
def list_subtitle_publications(
    response: Response,
    source: Literal["jellyfin"] = Query("jellyfin"),
    item_id: str = Query(min_length=1, max_length=256),
    media_source_id: str | None = Query(None, min_length=1, max_length=256),
    viewer: CatalogViewer = Depends(_viewer),
):
    """Discover verified sidecars without requiring a saved review to be selected."""
    from .catalog_files import source_file

    response.headers["Cache-Control"] = "private, no-store"
    found = source_file(viewer.db, viewer, item_id, media_source_id=media_source_id)
    try:
        media = Path(viewer.resolve(found.path)).resolve(strict=True)
        publications = (
            discover_publications(
                media,
                media_slot(media, found.media_source_id or ""),
                viewer=subtitle_publisher_id(viewer.access.principal.account.local_export_id.value),
            )
            if media.is_file()
            else []
        )
    except (OSError, ValueError):
        publications = []
    return SubtitlePublications(published_subtitles=publications)
