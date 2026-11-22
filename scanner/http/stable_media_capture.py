"""Identify and capture an authorized Jellyfin file at the physical version boundary."""

from collections.abc import Callable

from fastapi import HTTPException

from scanner.integrations.community.fingerprints import FileChangedError
from scanner.persistence.repository.media_identity_store import (
    find_title_id,
    identity_conflicts_title,
    observe_title,
    observe_version,
)
from scanner.runtime.media_fingerprints import content_digest


def file_digest(path: str, *, on_progress: Callable[[int, int], None] | None = None) -> str:
    """Read full content identity, reusing verified fingerprints for unchanged file state."""
    try:
        return (
            content_digest(path, on_progress=on_progress) if on_progress else content_digest(path)
        )
    except (FileChangedError, OSError, ValueError) as exc:
        raise HTTPException(409, "The selected media file could not be verified") from exc


def observe_target_version(db, prepared, *, digest: str | None = None) -> str:
    """Store the selected catalog binding and a strongly identified file version."""
    item = prepared.source.catalog_item
    metadata = item.media_identity if item else None
    known = find_title_id(
        db,
        server_id=prepared.source.server_id,
        jellyfin_id=prepared.source.item_id,
    )
    if known and identity_conflicts_title(db, known, metadata):
        raise HTTPException(409, "Jellyfin's provider IDs conflict with this Editarr title")
    checksum = digest or file_digest(prepared.media.resolved_path)
    media = prepared.store(db)
    observe_title(db, media, metadata)
    return observe_version(
        db,
        media=media,
        media_source_id=prepared.source.media_source_id,
        source_path=prepared.source.path,
        resolved_path=prepared.media.resolved_path,
        content_sha256=checksum,
    )
