"""Safe HTTP conflicts for current-owner review policy boundaries."""

from fastapi.responses import JSONResponse

from scanner.models import ReviewErrorDetail
from scanner.persistence.repository.review_policy import ReviewPolicyConflict
from scanner.persistence.repository.review_repairs import (
    CommunityScanUnavailable,
    ReviewNeedsRepair,
)


def install_review_policy_errors(app):
    async def conflict(_request, exc):
        return JSONResponse(
            status_code=409,
            content={
                "detail": ReviewErrorDetail(code=exc.code, message=str(exc)).model_dump(mode="json")
            },
            headers={"Cache-Control": "no-store"},
        )

    app.add_exception_handler(ReviewPolicyConflict, conflict)

    async def repair_conflict(_request, exc):
        detail = ReviewErrorDetail(code=exc.code, message=str(exc), repair=exc.repair)
        if isinstance(exc, CommunityScanUnavailable):
            detail.reason = exc.reason
            detail.transient = exc.transient
        return JSONResponse(
            status_code=409,
            content={"detail": detail.model_dump(mode="json", exclude_unset=True)},
            headers={"Cache-Control": "no-store"},
        )

    app.add_exception_handler(ReviewNeedsRepair, repair_conflict)
