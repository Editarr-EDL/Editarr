"""Actor-scoped saved-review lists and creation by authorized media target."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends

from scanner import api as root
from scanner.api import _review_error_responses
from scanner.db import MediaItemRow, SavedReviewRow
from scanner.models import (
    LibraryReviewCreate,
    SavedReview,
    SavedReviewSummary,
)
from scanner.persistence.repository.review_access import MissingReviewScan, ReviewAccess
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.targets import MediaRef
from scanner.security.scan_jobs.media import decide_scans

from .review_access import review_caller
from .review_targets import capture_target

router = APIRouter()


def list_target(caller, target, sort, direction):
    prepared = capture_target(caller, target)
    decision = decide_scans(
        caller.db, caller.access.principal.account, [prepared.media], datetime.now(UTC)
    )[0]
    require(caller.access, C.MEDIA_BROWSE, MediaRef(decision))
    media = (
        caller.db.query(MediaItemRow)
        .filter(
            MediaItemRow.server_id == prepared.source.server_id,
            MediaItemRow.jellyfin_id == prepared.source.item_id,
        )
        .one_or_none()
    )
    if media is None:
        return []
    visible_ids = []
    rows = caller.db.query(SavedReviewRow.id).filter(
        SavedReviewRow.media_item_id == media.id,
        SavedReviewRow.media_source_id == (prepared.source.media_source_id or ""),
    )
    for (review_id,) in rows:
        try:
            caller.visible(review_id)
        except AccessDenied:
            continue
        visible_ids.append(review_id)
    summaries = root.list_saved_reviews(
        caller.db,
        media_item_id=media.id,
        media_source_id=prepared.source.media_source_id,
        sort=sort,
        direction=direction,
        target_source=target.source,
        target_item_id=prepared.source.item_id,
        review_ids=visible_ids,
    )
    return [caller.present(summary) for summary in summaries]


def create_target(caller, target, data):
    prepared = capture_target(caller, target)

    def persist(db):
        media = prepared.store(db)
        duration = prepared.source.duration_seconds
        if duration is None:
            try:
                duration = root.get_video_duration(prepared.media.resolved_path)
            except (OSError, RuntimeError, ValueError):
                pass
        return root.create_saved_review(
            db,
            media_item_id=media.id,
            media_source_id=prepared.source.media_source_id,
            data=data,
            duration_seconds=duration,
            catalog_mcf_document=prepared.document,
            target_source=target.source,
            target_item_id=prepared.source.item_id,
        )

    try:
        return caller.present(
            caller.create(
                prepared.media,
                persist,
                scan_ids=[value.scan_id for value in data.memberships],
                missing_scan_error=MissingReviewScan,
            )
        )
    except ValueError as exc:
        caller.db.rollback()
        raise root._review_mutation_error(exc) from exc


@router.get(
    "/api/library-items/{item_id}/reviews",
    response_model=list[SavedReviewSummary],
    responses=_review_error_responses(404, 409, 502),
)
def api_list_jellyfin_reviews(
    item_id: str,
    media_source_id: str | None = None,
    sort: Literal["name", "updated_at"] = "updated_at",
    direction: Literal["asc", "desc"] = "desc",
    caller: ReviewAccess = Depends(review_caller),
):
    return list_target(
        caller,
        SimpleNamespace(source="jellyfin", item_id=item_id, media_source_id=media_source_id),
        sort,
        direction,
    )


@router.post(
    "/api/library-items/{item_id}/reviews",
    response_model=SavedReview,
    status_code=201,
    responses=_review_error_responses(404, 409, 502),
)
def api_create_jellyfin_review(
    item_id: str, request: LibraryReviewCreate, caller: ReviewAccess = Depends(review_caller)
):
    return create_target(
        caller,
        SimpleNamespace(
            source="jellyfin", item_id=item_id, media_source_id=request.media_source_id
        ),
        request,
    )
