"""Capture a review's authorized target before entering its write transaction."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scanner import api as root
from scanner.models import ReviewMcfDocument
from scanner.persistence.repository.review_access import ReviewAccess, not_found
from scanner.persistence.repository.scan_jobs import ScanMedia
from scanner.security.access.capability_ids import Capability as C
from scanner.server_paths import UnresolvablePath

from .catalog_access import CatalogViewer
from .catalog_files import SourceFile, source_file


@dataclass(frozen=True)
class ReviewTarget:
    source: SourceFile
    media: ScanMedia
    document: ReviewMcfDocument

    def store(self, db):
        """Store only the source record captured before locking."""
        if _local_path(db, self.source.path) != self.media.resolved_path:
            raise not_found()
        path = Path(self.media.resolved_path)
        if not path.is_file() or not os.access(path, os.R_OK):
            raise root._review_http_error(
                "media_unavailable", "The selected media file is unavailable"
            )
        return root.upsert_media_item(
            db,
            server_id=self.source.server_id,
            jellyfin_id=self.source.item_id,
            library_id=self.source.library_id,
            item_type=self.source.item_type,
            name=self.source.name,
            path=self.source.path,
            commit=False,
        )


def _local_path(db, server_path: str) -> str:
    try:
        return root.resolve_local_path(db, server_path).local_path
    except UnresolvablePath as exc:
        raise root._review_http_error(
            "media_unavailable", "The selected media file is unavailable"
        ) from exc


def capture_target(caller: ReviewAccess, target) -> ReviewTarget:
    """Resolve a Jellyfin item version without staging writes."""
    db = caller.db
    viewer = CatalogViewer(caller.access, db, C.MEDIA_BROWSE, datetime.now(UTC))
    try:
        source = source_file(db, viewer, target.item_id, media_source_id=target.media_source_id)
    finally:
        viewer.close()
    resolved = _local_path(db, source.path)
    facts = ScanMedia(source.server_id, source.item_id, source.library_id, resolved)
    _stored, selected_id, document = root._catalog_review_media(
        db,
        item_id=target.item_id,
        media_source_id=target.media_source_id,
        create=False,
        catalog_item=source.catalog_item,
    )
    if selected_id != source.media_source_id:
        raise not_found()
    return ReviewTarget(source, facts, document)
