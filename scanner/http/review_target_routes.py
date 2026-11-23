"""Actor-scoped saved-review lists and creation by authorized media target."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from scanner import api as root
from scanner.api import _review_error_responses
from scanner.db import MediaItemRow, ReviewMediaVersionRow, SavedReviewRow
from scanner.models import (
    LibraryReviewCreate,
    SavedReview,
    SavedReviewSummary,
)
from scanner.persistence.repository.media_identity_store import get_version, version_locators
from scanner.persistence.repository.review_access import MissingReviewScan, ReviewAccess
from scanner.persistence.repository.review_version_evidence import (
    ReviewMediaProof,
    use_review_media_proof,
    version_file_matches,
)
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from scanner.runtime.scan_version import ScanVersionChanged
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.targets import AccountRef, MediaRef, ResourceRef
from scanner.security.scan_jobs.media import decide_scans

from .review_access import review_caller
from .review_targets import capture_target
from .stable_media_capture import (
    cached_target_digest,
    observe_sampled_target_version,
    require_media_generation,
)

router = APIRouter()


def _matches_target_version(db, media_id, prepared, version_id: str) -> bool:
    """Require exact file evidence before listing reviews pinned to a version."""
    if not any(
        locator.media_item_id == media_id
        and locator.media_source_id == (prepared.source.media_source_id or "")
        for locator in version_locators(db, version_id)
    ):
        return False
    version = get_version(db, version_id)
    if version is None:
        return False
    if version.content_sha256:
        cached_target_digest(db, prepared)
    return version_file_matches(version, prepared.media.resolved_path, db=db)


def list_target(caller, target, sort, direction, stable_version_id=None):
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
    if stable_version_id is not None:
        rows = rows.join(
            ReviewMediaVersionRow, ReviewMediaVersionRow.review_id == SavedReviewRow.id
        )
        rows = rows.filter(ReviewMediaVersionRow.version_id == stable_version_id)
    for (review_id,) in rows:
        try:
            caller.visible(review_id)
        except AccessDenied:
            continue
        visible_ids.append(review_id)
    if not visible_ids:
        return []
    if stable_version_id is not None and not _matches_target_version(
        caller.db, media.id, prepared, stable_version_id
    ):
        return []
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
    principal = caller.access.principal
    require(
        caller.access,
        C.REVIEWS_CREATE_OWN,
        ResourceRef(
            AccountRef(principal.user_id, principal.account.role),
            caller.media_decision(prepared.media),
        ),
    )
    try:
        fingerprint = sampled_media_fingerprint(prepared.media.resolved_path)
        known = cached_target_digest(caller.db, prepared)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(409, "The selected media file could not be verified") from exc

    def persist(db):
        version_id = observe_sampled_target_version(
            db, prepared, fingerprint=fingerprint, known_content_sha256=known,
        )
        if data.stable_version_id is not None and version_id != data.stable_version_id:
            raise root._review_http_error("media_unavailable", "The selected media version changed")
        media = prepared.store(db)
        duration = prepared.source.duration_seconds
        if duration is None:
            try:
                duration = root.get_video_duration(prepared.media.resolved_path)
            except (OSError, RuntimeError, ValueError):
                pass
        require_media_generation(db, prepared.source.path, fingerprint.path, fingerprint.generation)
        with use_review_media_proof(db, ReviewMediaProof(fingerprint, known)):
            return root.create_saved_review(
                db,
                media_item_id=media.id,
                media_source_id=prepared.source.media_source_id,
                data=data,
                duration_seconds=duration,
                catalog_mcf_document=prepared.document,
                target_source=target.source,
                target_item_id=prepared.source.item_id,
                stable_version_id=version_id,
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
    except ScanVersionChanged as exc:
        caller.db.rollback()
        raise root._review_http_error(
            "media_unavailable", "The selected media version changed"
        ) from exc
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
    stable_version_id: str | None = None,
):
    return list_target(
        caller,
        SimpleNamespace(source="jellyfin", item_id=item_id, media_source_id=media_source_id),
        sort,
        direction,
        stable_version_id,
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
