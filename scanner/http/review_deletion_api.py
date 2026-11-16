"""Saved-review deletion with an explicit retain or remove choice per published file."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from scanner.modeling.model_review_deletion import (
    ReviewDeletionPreview,
    ReviewDeletionRequest,
    ReviewDeletionResult,
)
from scanner.persistence.repository.export_publication import ExportConflict
from scanner.persistence.repository.export_recovery import ExportRecoveryRequired
from scanner.persistence.repository.review_access import ReviewAccess
from scanner.persistence.repository.review_deletion import delete_review, preview_review_deletion
from scanner.persistence.repository.review_disposition import DispositionConflict, FileChoice
from scanner.repository import ReviewRevisionConflict

from .review_access import review_caller

router = APIRouter()
CHOICE_REQUIRED = "review_deletion_choice_required"


def disposition_error(exc: DispositionConflict) -> HTTPException:
    return HTTPException(
        422 if exc.code == CHOICE_REQUIRED else 409,
        detail={"code": exc.code, "message": str(exc)},
        headers={"cache-control": "no-store"},
    )


@router.get("/api/reviews/{review_id}/deletion", response_model=ReviewDeletionPreview)
def api_preview_review_deletion(review_id: str, caller: ReviewAccess = Depends(review_caller)):
    """Preview the review's visible published files and the choices the caller holds."""
    try:
        return preview_review_deletion(caller, review_id)
    except DispositionConflict as exc:
        raise disposition_error(exc) from exc


@router.post("/api/reviews/{review_id}/deletion", response_model=ReviewDeletionResult)
def api_delete_review_with_files(
    review_id: str, request: ReviewDeletionRequest, caller: ReviewAccess = Depends(review_caller)
):
    """Delete the previewed review, retaining or removing each listed file in one commit."""
    from .review_export_api import _error

    choices = [(file.export_id, FileChoice(file.choice)) for file in request.files]
    try:
        result = delete_review(
            caller,
            review_id,
            request.revision,
            confirmation=request.confirmation,
            choices=choices,
        )
    except DispositionConflict as exc:
        raise disposition_error(exc) from exc
    except (
        ExportConflict,
        ExportRecoveryRequired,
        ReviewRevisionConflict,
        OSError,
        SQLAlchemyError,
    ) as exc:
        raise _error(exc) from exc
    return ReviewDeletionResult(
        review_id=review_id, retained=list(result.retained), removed=list(result.removed)
    )
