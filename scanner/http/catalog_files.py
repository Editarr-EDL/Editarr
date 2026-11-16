"""Authorized media files named by Jellyfin item ID instead of a client path.

Callers that act on one file (filter candidates, audio tracks) name it by its
item ID and optional version ID. The record is read from Jellyfin and
authorized before any use; a client path only selects among the paths that
server reported. Stored media rows are authorized the same way, by
reading their source record again.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from scanner import api as root
from scanner.db import MediaItemRow, ScanRow
from scanner.integrations.jellyfin import JellyfinError
from scanner.models import JellyfinMediaItem
from scanner.security.media_access.matching import MediaTarget

from . import catalog_targets
from .catalog_access import CatalogViewer, not_found
from .catalog_library_api import (
    _cached_jellyfin_item,
    authorized_jellyfin_item,
    jellyfin_item_target,
)


@dataclass(frozen=True)
class SourceFile:
    """One authorized file as its source server reported it."""

    server_id: str
    item_id: str
    library_id: str | None
    item_type: str
    name: str
    path: str
    media_source_id: str | None
    media_source_name: str | None = None
    duration_seconds: float | None = None
    scene_name: str | None = None
    release_group: str | None = None
    media_format: str | None = None
    media_provider_id: str | None = None
    catalog_item: JellyfinMediaItem | None = None


def source_file(
    db: Session,
    viewer: CatalogViewer,
    item_id: str,
    *,
    media_source_id: str | None = None,
    path: str | None = None,
) -> SourceFile:
    """Read, authorize, and select one file; refuse what the caller may not reach.

    A version or path the server did not report is `not_found`, like a
    restricted item, so a request cannot tell them apart.
    """

    credentials = root.get_jellyfin_credentials(db)
    if not credentials:
        raise HTTPException(409, "Configure Jellyfin before choosing media")
    try:
        item, _library = authorized_jellyfin_item(viewer, credentials, item_id)
    except JellyfinError as exc:
        raise HTTPException(502, str(exc)) from exc
    versions = [version for version in item.versions if version.path]
    if media_source_id is not None:
        version = next((found for found in versions if found.id == media_source_id), None)
        if version is None:
            raise not_found()
    elif path is not None:
        version = next((found for found in versions if found.path == path), None)
        if version is None and path != item.path:
            raise not_found()
    else:
        primary = next((found for found in versions if found.path == item.path), None)
        version = primary or (versions[0] if versions else None)
    selected = version.path if version else item.path
    if not selected:
        raise not_found()
    return SourceFile(
        server_id=credentials[0],
        item_id=item.id,
        library_id=item.library_id,
        item_type=item.item_type,
        name=item.name,
        path=selected,
        media_source_id=version.id if version else None,
        media_source_name=version.name if version else None,
        duration_seconds=(version.duration_seconds if version else None)
        or item.duration_seconds,
        scene_name=version.scene_name if version else None,
        release_group=version.release_group if version else None,
        media_format=(version.media_format if version else None) or item.media_format,
        media_provider_id=item.media_provider_id,
        catalog_item=item,
    )


def stored_targets(
    db: Session, viewer: CatalogViewer, rows: list[catalog_targets.StoredMedia]
) -> list[MediaTarget | None]:
    """Each stored media row's target, rebuilt from its source server's current record."""

    jellyfin = root.get_jellyfin_credentials(db)

    def jellyfin_target(item_id: str) -> MediaTarget | None:
        if jellyfin is None:
            return None
        try:
            item = _cached_jellyfin_item(item_id, jellyfin)
            return jellyfin_item_target(viewer, jellyfin, item)
        except (JellyfinError, catalog_targets.SourceUnavailable):
            return None

    def target(row: catalog_targets.StoredMedia) -> MediaTarget | None:
        same_jellyfin = jellyfin is not None and row.server_id == jellyfin[0]
        return catalog_targets.stored_media_target(
            row,
            jellyfin=lambda item_id: jellyfin_target(item_id) if same_jellyfin else None,
        )

    return [target(row) for row in rows]


def stored_scans(db: Session, scan_ids: list[str]) -> dict[str, catalog_targets.StoredMedia]:
    """The stored media of each scan, keyed by scan ID; unknown IDs are absent."""

    found = (
        db.query(
            ScanRow.id,
            ScanRow.resolved_local_path,
            MediaItemRow.server_id,
            MediaItemRow.jellyfin_id,
        )
        .join(MediaItemRow, ScanRow.media_item_id == MediaItemRow.id)
        .filter(ScanRow.id.in_(scan_ids))
        .all()
    )
    return {
        scan_id: catalog_targets.StoredMedia(server_id, item_id, resolved)
        for scan_id, resolved, server_id, item_id in found
    }


def stored_media_item(db: Session, media_item_id: str) -> catalog_targets.StoredMedia | None:
    row = db.get(MediaItemRow, media_item_id)
    if row is None:
        return None
    return catalog_targets.StoredMedia(row.server_id, row.jellyfin_id, row.path)
