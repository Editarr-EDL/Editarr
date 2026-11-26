"""Jellyfin library browsing and catalog-item routes."""

import logging
import unicodedata
from collections.abc import Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from scanner import api as root
from scanner.api import (
    JELLYFIN_ITEM_CACHE,
    LIBRARY_ITEMS_CACHE,
    LIBRARY_ITEMS_CACHE_LOCK,
)
from scanner.db import get_db
from scanner.integrations.jellyfin import JellyfinError, JellyfinNotFound
from scanner.integrations.jellyfin_paths import library_of
from scanner.models import (
    ExportFormat,
    ExportStatus,
    JellyfinLibrary,
    JellyfinMediaItem,
    JellyfinMediaPage,
)
from scanner.runtime.application_paths import application_url
from scanner.security.access.capability_ids import Capability
from scanner.security.media_access.matching import MediaTarget
from scanner.security.media_access.resolver import LocalSubject

from . import catalog_targets
from .catalog_access import (
    CatalogViewer,
    catalog_viewer,
    jellyfin_record,
    library_record,
    not_found,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_browse = catalog_viewer(Capability.MEDIA_BROWSE)
_BROWSABLE_ITEM_TYPES = ("Movie", "Series", "Video", "MusicVideo")


@router.get("/api/libraries", response_model=list[JellyfinLibrary])
def api_list_jellyfin_libraries(
    db: Session = Depends(get_db), viewer: CatalogViewer = Depends(_browse)
):
    """Return the libraries the caller may open, with connection failures mapped to HTTP errors."""
    credentials = root.get_jellyfin_credentials(db)
    if not credentials:
        raise HTTPException(409, "Configure Jellyfin before browsing libraries")
    try:
        with root.JellyfinClient(*credentials) as client:
            libraries = client.libraries()
    except JellyfinError as exc:
        root.record_jellyfin_connection(db, str(exc))
        raise HTTPException(502, str(exc)) from exc
    root.record_jellyfin_connection(db, None)
    if viewer.is_owner:
        return libraries
    return [
        library_record(library, viewer)
        for library in libraries
        if viewer.library_visible(library.id)
    ]


def jellyfin_item_target(
    viewer: CatalogViewer,
    credentials: tuple[str, str],
    item: JellyfinMediaItem,
    library: str | None = None,
) -> MediaTarget | None:
    """The item's target; a local account's item without a known library is placed by path."""
    if library is None and isinstance(viewer.subject, LocalSubject):
        library = library_of(item.path, catalog_targets.libraries(credentials))
    server = viewer.jellyfin_server(catalog_targets.connection_key(credentials[0]))
    return catalog_targets.jellyfin_item_target(server, item, library, viewer.resolve)


def authorized_jellyfin_item(
    viewer: CatalogViewer, credentials: tuple[str, str], item_id: str
) -> tuple[JellyfinMediaItem, str | None]:
    """Read one item and refuse it as `not_found` unless the caller may reach it.

    Returns the item and, for a local account, the library that granted it.
    """
    try:
        item = _cached_jellyfin_item(item_id, credentials)
    except JellyfinNotFound as exc:
        raise not_found() from exc
    if viewer.is_owner:
        return item, None
    try:
        target = jellyfin_item_target(viewer, credentials, item)
    except catalog_targets.SourceUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    viewer.require(target)
    return item, target.source.source_id if target and target.source else None


def _library_sort_value(item: JellyfinMediaItem, sort_by: str) -> tuple[object, ...]:
    """Produce a stable sort key for a library item and requested ordering."""
    if sort_by == "rating":
        return (
            item.official_rating is None,
            _normalized_title(item.official_rating or ""),
            _normalized_title(item.name),
            item.id,
        )
    if sort_by == "duration":
        return (
            item.duration_seconds is None,
            item.duration_seconds or 0,
            _normalized_title(item.name),
            item.id,
        )
    if sort_by == "year":
        return (item.year is None, item.year or 0, _normalized_title(item.name), item.id)
    if sort_by == "type":
        return (item.item_type.casefold(), _normalized_title(item.sort_name or item.name), item.id)
    # Titles outside A–Z (digits, symbols, other scripts) form one "#" group before A.
    return (_title_letter(item) != "#", _normalized_title(item.sort_name or item.name), item.id)


def _normalized_title(value: str) -> str:
    """Fold a title for matching and ordering: accents removed, case folded, dotless ı read as i."""
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value).casefold().replace("ı", "i")
        if not unicodedata.combining(character)
    )


def _title_letter(item: JellyfinMediaItem) -> str:
    """Return the A–Z initial a title sorts under, or "#" for anything else.

    Accents are removed and case folded first, so "Élan" is "E" and "ß" is "S" ("ss").
    """
    initial = _normalized_title(item.sort_name or item.name)[:1].upper()
    return initial if "A" <= initial <= "Z" else "#"


def _title_letter_offsets(items: list[JellyfinMediaItem]) -> dict[str, int]:
    """Map each initial to the index of its first item in an already sorted list."""
    offsets: dict[str, int] = {}
    for index, item in enumerate(items):
        offsets.setdefault(_title_letter(item), index)
    return offsets


def _library_browse_items(
    library_id: str, items: list[JellyfinMediaItem]
) -> list[JellyfinMediaItem]:
    """Choose library-root series or scan-capable video items for the grid.

    Jellyfin's recursive library query also returns organizational containers,
    seasons, and episodes. TV libraries should start at series, while movie and
    generic-video libraries should show only video-backed items—not folders.
    """
    series = [item for item in items if item.item_type == "Series"]
    if series:
        return series
    containers = {
        "BoxSet",
        "CollectionFolder",
        "Folder",
        "Genre",
        "MusicAlbum",
        "MusicArtist",
        "Person",
        "Playlist",
        "Season",
        "Series",
        "Studio",
        "UserView",
    }
    return [
        item
        for item in items
        if item.item_type not in containers
        and item.item_type not in {"Episode", "Season"}
        and bool(item.path)
        and (
            item.parent_id in {None, library_id}
            or item.item_type in {"Movie", "Video", "MusicVideo"}
        )
    ]


def _with_library_item_state(
    db: Session, *, server_id: str, items: list[JellyfinMediaItem], caller=None
) -> list[JellyfinMediaItem]:
    """Add scan and export state to a Jellyfin catalog item."""
    statuses = root.library_item_export_statuses(
        db, server_id=server_id, jellyfin_item_ids=[item.id for item in items]
    )
    all_version_statuses = root.library_item_version_export_statuses_for_items(
        db, server_id=server_id, jellyfin_item_ids=[item.id for item in items]
    )
    decorated: list[JellyfinMediaItem] = []
    for item in items:
        item_exports = statuses.get(item.id, ExportStatus())
        version_statuses = all_version_statuses.get(item.id, {})
        if caller is not None:
            from scanner.persistence.repository.export_discovery import catalog_publication_status

            item_exports = catalog_publication_status(
                caller,
                item_exports,
                server_id=server_id,
                item_id=item.id,
                item_type=item.item_type,
            )
            version_statuses = {
                version.id or "": catalog_publication_status(
                    caller,
                    version_statuses.get(version.id or "", ExportStatus()),
                    server_id=server_id,
                    item_id=item.id,
                    item_type=item.item_type,
                    media_source_id=version.id or "",
                )
                for version in item.versions
            }
        decorated.append(
            item.model_copy(
                update={
                    "image_url": application_url(
                        root.active_deployment_snapshot(),
                        f"/api/library-items/{quote(item.id, safe='')}/image",
                    ),
                    "exports": item_exports,
                    "versions": [
                        version.model_copy(
                            update={
                                "exports": version_statuses.get(version.id or "", ExportStatus()),
                            }
                        )
                        for version in item.versions
                    ],
                }
            )
        )
    return decorated


def _cached_library_items(
    library_id: str, credentials: tuple[str, str], *, refresh: bool
) -> list[JellyfinMediaItem]:
    """Keep Jellyfin catalog reads stable while users browse one library."""
    cache_key = (credentials[0], library_id)
    with LIBRARY_ITEMS_CACHE_LOCK:
        cached = LIBRARY_ITEMS_CACHE.get(cache_key)
    if cached is not None and not refresh:
        return cached
    with root.JellyfinClient(*credentials) as client:
        items = client.all_items(
            library_id,
            recursive=False,
            include_item_types=_BROWSABLE_ITEM_TYPES,
        )
    with LIBRARY_ITEMS_CACHE_LOCK:
        LIBRARY_ITEMS_CACHE[cache_key] = items
        for item in items:
            JELLYFIN_ITEM_CACHE[(credentials[0], item.id)] = item
    return items


def _cached_jellyfin_item(item_id: str, credentials: tuple[str, str]) -> JellyfinMediaItem:
    """Fetch one Jellyfin item through the short-lived library-item cache."""
    cache_key = (credentials[0], item_id)
    with LIBRARY_ITEMS_CACHE_LOCK:
        cached = JELLYFIN_ITEM_CACHE.get(cache_key)
    if cached is not None and cached._detail_loaded:
        return cached
    with root.JellyfinClient(*credentials) as client:
        item = client.item(item_id)
    with LIBRARY_ITEMS_CACHE_LOCK:
        JELLYFIN_ITEM_CACHE[cache_key] = item
    return item


@router.get("/api/libraries/{library_id}/items", response_model=JellyfinMediaPage)
def api_list_jellyfin_library_items(
    library_id: str,
    start_index: int = 0,
    limit: int = 100,
    sort_by: str = "title",
    sort_direction: str = "asc",
    edl_filter: str = "all",
    rating_filter: str = "all",
    resolution_filter: str = "all",
    search: str | None = None,
    refresh: bool = False,
    db: Session = Depends(get_db),
    viewer: CatalogViewer = Depends(_browse),
):
    """Fetch, authorize, filter, sort, and page items in a Jellyfin library.

    Items the caller may not reach are dropped before totals, facets, export
    state, and the page are computed.
    """
    if start_index < 0 or not 1 <= limit <= 500:
        raise HTTPException(400, "start_index must be positive and limit must be between 1 and 500")
    if sort_by not in {"title", "year", "rating", "duration", "type"}:
        raise HTTPException(400, "sort_by must be title, year, rating, duration, or type")
    if sort_direction not in {"asc", "desc"}:
        raise HTTPException(400, "sort_direction must be asc or desc")
    if edl_filter not in {"all", "exported", "not_exported"}:
        raise HTTPException(400, "edl_filter must be all, exported, or not_exported")
    credentials = root.get_jellyfin_credentials(db)
    if not credentials:
        raise HTTPException(409, "Configure Jellyfin before browsing libraries")
    if not viewer.library_visible(library_id):
        raise not_found()
    title_query = (search or "").strip()
    search_value = _normalized_title(title_query)
    try:
        items = _cached_library_items(library_id, credentials, refresh=refresh)
        if search_value:
            # Jellyfin can return a title match omitted from its unfiltered listing.
            # Keep the library's normal facets and local accent-insensitive matching.
            with root.JellyfinClient(*credentials) as client:
                matches = client.all_items(
                    library_id,
                    recursive=False,
                    include_item_types=_BROWSABLE_ITEM_TYPES,
                    search_term=title_query,
                )
            by_id = {item.id: item for item in items}
            by_id.update((item.id, item) for item in matches)
            items = list(by_id.values())
    except JellyfinNotFound as exc:
        raise not_found() from exc
    except JellyfinError as exc:
        root.record_jellyfin_connection(db, str(exc))
        raise HTTPException(502, str(exc)) from exc
    root.record_jellyfin_connection(db, None)
    reachable = viewer.visible(
        _library_browse_items(library_id, items),
        lambda item: jellyfin_item_target(viewer, credentials, item, library_id),
    )
    decorated = _with_library_item_state(
        db,
        server_id=credentials[0],
        items=reachable,
        caller=viewer.export_caller,
    )
    rating_values = sorted(
        {item.official_rating for item in decorated if item.official_rating},
        key=_normalized_title,
    )
    resolution_order = {"4K": 0, "1440p": 1, "1080p": 2, "720p": 3, "480p": 4, "SD": 5}
    resolution_values = sorted(
        {item.resolution for item in decorated if item.resolution},
        key=lambda value: resolution_order.get(value, len(resolution_order)),
    )
    filtered = [
        item
        for item in decorated
        if (not search_value or search_value in _normalized_title(item.name))
        and (
            edl_filter == "all"
            or item.exports.exports[ExportFormat.EDL].present == (edl_filter == "exported")
        )
        and (rating_filter == "all" or item.official_rating == rating_filter)
        and (resolution_filter == "all" or item.resolution == resolution_filter)
    ]
    filtered.sort(
        key=lambda item: _library_sort_value(item, sort_by),
        reverse=sort_direction == "desc",
    )
    return JellyfinMediaPage(
        items=[
            jellyfin_record(item, viewer) for item in filtered[start_index : start_index + limit]
        ],
        total_count=len(filtered),
        rating_values=rating_values,
        resolution_values=resolution_values,
        title_letter_offsets=_title_letter_offsets(filtered) if sort_by == "title" else {},
    )


@router.get("/api/library-items/{item_id}", response_model=JellyfinMediaItem)
def api_get_jellyfin_library_item(
    item_id: str, db: Session = Depends(get_db), viewer: CatalogViewer = Depends(_browse)
):
    """Return a reachable catalog item with its scan and export state."""
    credentials = root.get_jellyfin_credentials(db)
    if not credentials:
        raise HTTPException(409, "Configure Jellyfin before browsing libraries")
    try:
        item, _library = authorized_jellyfin_item(viewer, credentials, item_id)
    except JellyfinError as exc:
        root.record_jellyfin_connection(db, str(exc))
        raise HTTPException(502, str(exc)) from exc
    root.record_jellyfin_connection(db, None)
    item = _with_library_item_state(
        db,
        server_id=credentials[0],
        items=[item],
        caller=viewer.export_caller,
    )[0]
    return jellyfin_record(item, viewer)


def _reachable_children(
    db: Session,
    viewer: CatalogViewer,
    item_id: str,
    read: Callable[[object], list[JellyfinMediaItem]],
) -> list[JellyfinMediaItem]:
    """Authorize the parent, read its children, and keep the reachable ones.

    A local account's children are in the library that granted the parent.
    """
    credentials = root.get_jellyfin_credentials(db)
    if not credentials:
        raise HTTPException(409, "Configure Jellyfin before browsing libraries")
    try:
        _parent, library = authorized_jellyfin_item(viewer, credentials, item_id)
        with root.JellyfinClient(*credentials) as client:
            items = read(client)
    except JellyfinError as exc:
        root.record_jellyfin_connection(db, str(exc))
        raise HTTPException(502, str(exc)) from exc
    root.record_jellyfin_connection(db, None)
    with LIBRARY_ITEMS_CACHE_LOCK:
        for item in items:
            JELLYFIN_ITEM_CACHE[(credentials[0], item.id)] = item
    reachable = viewer.visible(
        items, lambda child: jellyfin_item_target(viewer, credentials, child, library)
    )
    decorated = _with_library_item_state(
        db,
        server_id=credentials[0],
        items=reachable,
        caller=viewer.export_caller,
    )
    return [jellyfin_record(item, viewer) for item in decorated]


@router.get("/api/library-items/{item_id}/children", response_model=list[JellyfinMediaItem])
def api_list_jellyfin_library_item_children(
    item_id: str, db: Session = Depends(get_db), viewer: CatalogViewer = Depends(_browse)
):
    """Return the reachable direct children of a reachable Jellyfin item."""
    return _reachable_children(db, viewer, item_id, lambda client: client.children(item_id))


@router.get("/api/library-items/{item_id}/seasons", response_model=list[JellyfinMediaItem])
def api_list_jellyfin_series_seasons(
    item_id: str, db: Session = Depends(get_db), viewer: CatalogViewer = Depends(_browse)
):
    """Return the reachable seasons of a reachable Jellyfin series."""
    return _reachable_children(db, viewer, item_id, lambda client: client.seasons(item_id))
