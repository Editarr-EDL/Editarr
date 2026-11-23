"""Identify and capture an authorized Jellyfin file at the physical version boundary."""

from pathlib import Path

from fastapi import HTTPException

from scanner.persistence.repository.media_identity_store import (
    find_title_id,
    identity_conflicts_title,
    observe_sampled_version,
    observe_title,
)
from scanner.persistence.write_lock import write_lock
from scanner.repository import resolve_local_path
from scanner.runtime.media_fingerprints import SampledMediaFingerprint, cached_content_digest
from scanner.runtime.scan_version import ScanVersionChanged, completed_scan_digest, file_generation


def require_media_generation(
    db, source_path: str, resolved: Path, generation: tuple[int, ...]
) -> None:
    """Require the captured generation at the current mapped Jellyfin path."""
    try:
        current = Path(resolve_local_path(db, source_path).local_path).resolve(strict=True)
        if current == resolved and file_generation(str(current)) == generation:
            return
    except (OSError, RuntimeError, ValueError):
        pass
    raise ScanVersionChanged("The selected media file changed")


def cached_target_digest(db, prepared, *, audio_evidence=None) -> str | None:
    """Read exact generation evidence for an already authorized target without hashing."""
    path = Path(prepared.media.resolved_path)
    return cached_content_digest(path) or completed_scan_digest(
        db,
        server_id=prepared.source.server_id,
        item_id=prepared.source.item_id,
        media_source_id=prepared.source.media_source_id,
        path=path,
        audio_evidence=audio_evidence,
    )


def _target_metadata(db, prepared):
    item = prepared.source.catalog_item
    metadata = item.media_identity if item else None
    known = find_title_id(
        db,
        server_id=prepared.source.server_id,
        jellyfin_id=prepared.source.item_id,
    )
    if known and identity_conflicts_title(db, known, metadata):
        raise HTTPException(409, "Jellyfin's provider IDs conflict with this Editarr title")
    return metadata


def observe_sampled_target_version(
    db,
    prepared,
    *,
    fingerprint: SampledMediaFingerprint,
    known_content_sha256: str | None = None,
) -> str:
    """Store one sampled version only while its authorized mapping and generation match."""
    with write_lock(db):
        require_media_generation(db, prepared.source.path, fingerprint.path, fingerprint.generation)
        metadata = _target_metadata(db, prepared)
        media = prepared.store(db)
        observe_title(db, media, metadata)
        return observe_sampled_version(
            db,
            media=media,
            media_source_id=prepared.source.media_source_id,
            source_path=prepared.source.path,
            fingerprint=fingerprint,
            known_content_sha256=known_content_sha256,
        )
