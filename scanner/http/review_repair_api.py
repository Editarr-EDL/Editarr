"""Human-directed recovery of saved reviews with missing scan references."""

from fastapi import APIRouter, Body, Depends

from scanner.api import _review_error_responses
from scanner.db import SavedReviewRow
from scanner.models import SavedReviewSummary
from scanner.persistence.repository.repository_exports import _review_target
from scanner.persistence.repository.review_access import SAVE, ReviewAccess
from scanner.persistence.repository.review_repairs import recover_review

from .review_access import review_caller
from .review_targets import capture_target

router = APIRouter()


def repair_target(caller, review_id):
    caller.visible(review_id)
    row = caller.db.get(SavedReviewRow, review_id)
    return capture_target(caller, _review_target(row))


@router.post(
    "/api/reviews/{review_id}/repair/recover",
    response_model=SavedReviewSummary,
    responses=_review_error_responses(403, 404, 409, 502),
)
def recover(review_id: str, caller: ReviewAccess = Depends(review_caller)):
    prepared = repair_target(caller, review_id)
    result = caller.run(review_id, SAVE, recover_review, prepared=prepared, repair=True)
    return caller.present(result)


@router.post(
    "/api/reviews/{review_id}/repair/community",
    response_model=SavedReviewSummary,
    responses=_review_error_responses(403, 404, 409, 502),
)
async def community(
    review_id: str,
    caller: ReviewAccess = Depends(review_caller),
    confirmed: bool = Body(default=False, embed=True, strict=True),
):
    from scanner.integrations.community.local_downloads import drained_thread
    from scanner.integrations.community.review_repairs import (
        Restored,
        Transient,
        repair_from_community,
    )
    from scanner.persistence.repository.review_repairs import (
        CommunityScanUnavailable,
        repair_summary,
    )

    from .community_downloads_api import download_service

    prepared = await drained_thread(repair_target, caller, review_id)
    outcome = await repair_from_community(
        caller,
        review_id,
        prepared,
        lambda: download_service(caller.db, caller.access),
        confirmed=confirmed,
    )
    if isinstance(outcome, Restored):
        return caller.present(outcome.review)
    raise CommunityScanUnavailable(
        repair_summary(caller.db, review_id),
        outcome.reason,
        transient=isinstance(outcome, Transient),
    )
