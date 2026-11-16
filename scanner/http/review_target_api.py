"""Review target resolution and creation routes."""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from scanner import api as root
from scanner.db import MediaItemRow
from scanner.integrations.jellyfin import JellyfinError
from scanner.models import (
    JellyfinMediaItem,
    McfImportIssue,
    McfMetadataSeedCandidate,
    McfWorkType,
    ReviewErrorDetail,
    ReviewMcfDocument,
    SavedReview,
)
from scanner.repository import (
    CreditsAcceptanceError,
    McfMetadataSourceRequired,
    MediaRuntimeUnavailable,
    ReviewMediaUnavailable,
    ReviewMembershipError,
    ReviewRevisionConflict,
    get_saved_review,
    list_saved_reviews,
    resolve_local_path,
    upsert_media_item,
)

router = APIRouter()


def _review_http_error(
    code: str,
    message: str,
    status_code: int = 409,
    *,
    line_number: int | None = None,
    line_text: str | None = None,
    references=None,
    metadata_candidates: list[McfMetadataSeedCandidate] | None = None,
    filter_ids=None,
    document_fields: list[str] | None = None,
    import_issues: list[McfImportIssue] | None = None,
) -> HTTPException:
    """Translate repository review failures into stable HTTP error responses."""
    return HTTPException(
        status_code,
        detail=ReviewErrorDetail(
            code=code,
            message=message,
            line_number=line_number,
            line_text=line_text,
            references=references,
            metadata_candidates=metadata_candidates or [],
            filter_ids=filter_ids or [],
            document_fields=document_fields or [],
            import_issues=import_issues or [],
        ).model_dump(mode="json"),
    )


def _review_mutation_error(exc: ValueError) -> HTTPException:
    """Map a review mutation failure to its public HTTP status and detail."""
    from scanner.persistence.repository.review_access import MissingReviewScan
    from scanner.persistence.repository.review_deletion import PublishedReviewDeletionBlocked
    from scanner.persistence.repository.review_policy import ReviewPolicyConflict

    if isinstance(exc, MissingReviewScan):
        return _review_http_error("scan_not_found", "A selected scan is no longer available", 404)
    if isinstance(exc, ReviewPolicyConflict):
        return _review_http_error(exc.code, str(exc))
    if isinstance(exc, PublishedReviewDeletionBlocked):
        return _review_http_error("published_review_retention_required", str(exc))
    if isinstance(exc, CreditsAcceptanceError):
        return _review_http_error(exc.code, str(exc), exc.status_code)
    if isinstance(exc, ReviewRevisionConflict):
        return _review_http_error("stale_review_revision", str(exc))
    if isinstance(exc, McfMetadataSourceRequired):
        return _review_http_error(
            "mcf_metadata_source_required",
            str(exc),
            metadata_candidates=exc.candidates,
        )
    if isinstance(exc, ReviewMembershipError):
        return _review_http_error("invalid_membership", str(exc))
    if isinstance(exc, MediaRuntimeUnavailable):
        return _review_http_error("media_runtime_unavailable", str(exc))
    if isinstance(exc, ReviewMediaUnavailable):
        return _review_http_error("media_unavailable", str(exc))
    return _review_http_error("invalid_review_filter", str(exc))


def _catalog_review_media(
    db: Session,
    *,
    item_id: str,
    media_source_id: str | None,
    create: bool,
    persist: bool = True,
    catalog_item: JellyfinMediaItem | None = None,
) -> tuple[MediaItemRow | None, str | None]:
    """Resolve catalog metadata and the physical version for a review target."""
    credentials = root.get_jellyfin_credentials(db)
    if not credentials:
        raise _review_http_error("media_unavailable", "Configure Jellyfin first")
    try:
        if catalog_item is None:
            with root.JellyfinClient(*credentials) as client:
                item = client.item(item_id)
        else:
            item = catalog_item
    except JellyfinError as exc:
        raise _review_http_error(
            "media_unavailable", "Catalog item is unavailable", 502
        ) from exc
    selected = (
        next(
            (version for version in item.versions if version.id == media_source_id),
            None,
        )
        if media_source_id
        else None
    )
    if media_source_id and selected is None:
        raise _review_http_error(
            "media_unavailable", "The selected media version is unavailable"
        )
    if media_source_id is None:
        if len(item.versions) > 1:
            raise _review_http_error(
                "media_unavailable", "Select a media version for this item"
            )
        if len(item.versions) == 1:
            selected = item.versions[0]
    source_path = selected.path if selected else item.path
    server_id = credentials[0]
    item_type = item.item_type
    library_id = item.library_id
    name = item.name
    stored_path = source_path
    selected_id = selected.id if selected else None
    catalog_document = ReviewMcfDocument(
        title=item.name,
        year=item.year,
        work_type=_mcf_work_type(item.item_type),
        season=(
            item.season_number
            if item.item_type.casefold() == "episode"
            and item.season_number is not None
            and item.season_number > 0
            else None
        ),
        episode=(
            item.index_number
            if item.item_type.casefold() == "episode"
            and item.index_number is not None
            and item.index_number > 0
            else None
        ),
        release=(selected.edition or selected.name) if selected else None,
        material_start_seconds=0,
        material_end_seconds=(selected.duration_seconds if selected else item.duration_seconds),
    )
    if not create:
        row = (
            db.query(MediaItemRow)
            .filter(
                MediaItemRow.server_id == server_id,
                MediaItemRow.jellyfin_id == item_id,
            )
            .one_or_none()
        )
        return row, selected_id, catalog_document
    if not source_path:
        raise _review_http_error("media_unavailable", "The selected item has no media file")
    try:
        resolution = resolve_local_path(db, source_path)
    except ValueError as exc:
        raise _review_http_error(
            "media_unavailable", "The selected media file is unavailable"
        ) from exc
    local_path = Path(resolution.local_path)
    if not local_path.is_file() or not os.access(local_path, os.R_OK):
        raise _review_http_error("media_unavailable", "The selected media file is unavailable")
    if not persist:
        return (
            MediaItemRow(
                server_id=server_id,
                jellyfin_id=item_id,
                library_id=library_id,
                item_type=item_type,
                name=name,
                path=stored_path,
            ),
            selected_id,
            catalog_document,
        )
    return (
        upsert_media_item(
            db,
            server_id=server_id,
            jellyfin_id=item_id,
            library_id=library_id,
            item_type=item_type,
            name=name,
            path=stored_path,
            commit=False,
        ),
        selected_id,
        catalog_document,
    )


def _mcf_work_type(item_type: str) -> McfWorkType | None:
    return {
        "movie": McfWorkType.MOVIE,
        "series": McfWorkType.SERIES,
        "season": McfWorkType.SERIES,
        "episode": McfWorkType.EPISODE,
    }.get(item_type.casefold())


def _list_catalog_reviews(
    db: Session,
    *,
    source: str,
    item_id: str,
    media_source_id: str | None,
    sort: str,
    direction: str,
):
    """Return saved reviews for the resolved catalog media version."""
    media, selected_id, _catalog_document = root._catalog_review_media(
        db,
        item_id=item_id,
        media_source_id=media_source_id,
        create=False,
    )
    if media is None:
        return []
    return list_saved_reviews(
        db,
        media_item_id=media.id,
        media_source_id=selected_id,
        sort=sort,
        direction=direction,
        target_source=source,
        target_item_id=item_id,
    )


def _refresh_saved_review_target(db: Session, review_id: str) -> SavedReview | None:
    """Refresh a saved review target from current catalog metadata."""
    review = get_saved_review(db, review_id)
    if review is None:
        return None
    target = review.target
    root._catalog_review_media(
        db,
        item_id=target.item_id,
        media_source_id=target.media_source_id,
        create=True,
    )
    return review
