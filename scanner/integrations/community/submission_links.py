"""The master link a submission projects against, on the source's local timeline."""

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import CommunityMasterFilterRow, ScanRow
from scanner.modeling.community_filter_proposals import LinkedFilter
from scanner.persistence.repository.community_master_filters import master_values
from scanner.persistence.repository.community_master_links import alignment_of, get_link

from .donation_sources import SourceRef, canonical
from .submission_projection import LinkProjection
from .transport import CommunityFailure

if TYPE_CHECKING:
    from .submissions import SubmissionRequest


def _observed_scan_version(db: Session, scan_id: str) -> str | None:
    """The version observed at an unbound scan's file, while that file is unchanged."""
    from scanner.db import MediaItemRow
    from scanner.persistence.repository.media_identity_store import (
        find_observed_version,
        find_title_id,
    )
    from scanner.runtime.file_generation import file_generation

    scan = db.get(ScanRow, scan_id)
    media = db.get(MediaItemRow, scan.media_item_id) if scan is not None else None
    if media is None:
        return None
    title_id = find_title_id(db, server_id=media.server_id, jellyfin_id=media.jellyfin_id)
    path = Path(scan.resolved_local_path)
    try:
        generation = file_generation(path.stat(), path)
    except OSError:
        return None
    if title_id is None:
        return None
    return find_observed_version(db, title_id=title_id, path=path, generation=generation)


def source_version_id(db: Session, source: SourceRef) -> str | None:
    """The local media version a scan or saved review is bound to, if any.

    An unbound scan uses the version observed at its unchanged file; an unbound review
    uses its first member scan's.
    """
    from scanner.db import ReviewScanMembershipRow
    from scanner.persistence.repository.media_identity_store import (
        review_version_id,
        scan_version_id,
    )

    if source.kind == "scan":
        return scan_version_id(db, source.identifier) or _observed_scan_version(
            db, source.identifier
        )
    bound = review_version_id(db, source.identifier)
    if bound is not None:
        return bound
    scan_id = db.scalar(
        select(ReviewScanMembershipRow.scan_id)
        .where(ReviewScanMembershipRow.review_id == source.identifier)
        .order_by(ReviewScanMembershipRow.position)
        .limit(1)
    )
    return None if scan_id is None else source_version_id(db, SourceRef("scan", scan_id))


def link_projection(
    db: Session, request: "SubmissionRequest", source: SourceRef
) -> LinkProjection | None:
    """The account's served link filters on the local timeline, for scan cues to propose.

    The link must follow the source's own local version on the client's service
    (`link_unavailable`). A link still awaiting alignment has no local timeline yet
    and is not projected.
    """
    if request.link_id is None:
        return None
    link = get_link(db, request.owner_id, request.link_id)
    if (
        link is None
        or link.link_state == "replaced"
        or link.service_url != request.service_url
        or link.media_version_id != source_version_id(db, source)
    ):
        raise CommunityFailure("link_unavailable")
    if link.alignment_state == "required":
        return None
    alignment = alignment_of(link)
    rows = db.scalars(
        select(CommunityMasterFilterRow)
        .where(CommunityMasterFilterRow.link_id == link.id, CommunityMasterFilterRow.present)
        .order_by(CommunityMasterFilterRow.filter_id)
    )
    filters = []
    for row in rows:
        values = master_values(row, alignment)
        filters.append(
            LinkedFilter(
                row.filter_id,
                row.category,
                values["startMs"],
                values["endMs"],
                values["subcategory"],
                values["severity"],
                values["action"],
            )
        )
    # The hint names the revision the local rows were last applied from.
    return LinkProjection(
        link.community_version_id, link.applied_revision, alignment, tuple(filters)
    )


def link_guard(link: LinkProjection | None) -> str:
    """A digest of the projected link state; approval refuses a changed link."""
    return hashlib.sha256(canonical(repr(link))).hexdigest()
