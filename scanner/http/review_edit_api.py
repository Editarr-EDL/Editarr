"""Saved-review editing, preview, and export routes."""

from fastapi import APIRouter, Depends, Response

from scanner import api as root
from scanner.api import _review_error_responses
from scanner.models import (
    ExportFormat,
    McfPreflight,
    ReviewDuplicate,
    ReviewExportResponse,
    ReviewRename,
    ReviewRetimeRequest,
    ReviewRetimeResult,
    ReviewSave,
    SavedReview,
)
from scanner.persistence.repository.review_access import (
    COPY,
    PREVIEW,
    RENAME,
    SAVE,
    TIMING,
    VIEW,
    ReviewAccess,
)
from scanner.persistence.repository.review_policy import ReviewPolicyConflict
from scanner.repository import (
    IncompleteMcfReview,
    ReviewMembershipError,
    ReviewRetimeError,
    ReviewRevisionConflict,
    duplicate_saved_review,
    get_saved_review,
    preflight_saved_review_mcf,
    rename_saved_review,
    render_saved_review_filter_preview,
    retime_saved_review_draft,
    save_saved_review,
)

from .review_access import review_caller, run_review
from .review_export_api import PublicationRequest, compatibility_export

router = APIRouter()


@router.get(
    "/api/reviews/{review_id}",
    response_model=SavedReview,
    responses=_review_error_responses(404, 409),
)
def api_get_saved_review(review_id: str, caller: ReviewAccess = Depends(review_caller)):
    review = caller.run(review_id, VIEW, get_saved_review)
    if review is None:
        raise root._review_http_error("review_not_found", "Review not found", 404)
    return caller.present(review)


@router.put(
    "/api/reviews/{review_id}",
    response_model=SavedReview,
    responses=_review_error_responses(404, 409, 502),
)
def api_save_saved_review(
    review_id: str, request: ReviewSave, caller: ReviewAccess = Depends(review_caller)
):
    """Persist a review draft if its revision still matches the saved review."""
    db = caller.db
    try:
        review = run_review(
            caller,
            review_id,
            SAVE,
            save_saved_review,
            request,
            draft=request,
            scan_ids=[value.scan_id for value in request.memberships],
        )
    except ValueError as exc:
        db.rollback()
        raise root._review_mutation_error(exc) from exc
    if review is None:
        raise root._review_http_error("review_not_found", "Review not found", 404)
    return caller.present(review)


@router.post(
    "/api/reviews/{review_id}/retime",
    response_model=ReviewRetimeResult,
    responses=_review_error_responses(404, 409, 422, 502),
)
def api_retime_saved_review(
    review_id: str, request: ReviewRetimeRequest, caller: ReviewAccess = Depends(review_caller)
):
    """Preview retimed review filters without changing the saved revision."""
    db = caller.db
    try:
        result = run_review(
            caller,
            review_id,
            TIMING,
            retime_saved_review_draft,
            request,
            scan_ids=[value.scan_id for value in request.memberships],
        )
    except ReviewRetimeError as exc:
        db.rollback()
        raise root._review_http_error(
            exc.code,
            str(exc),
            422,
            filter_ids=exc.filter_ids,
            document_fields=exc.document_fields,
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise root._review_mutation_error(exc) from exc
    if result is None:
        raise root._review_http_error("review_not_found", "Review not found", 404)
    return result


@router.patch(
    "/api/reviews/{review_id}",
    response_model=SavedReview,
    responses=_review_error_responses(404, 409),
)
def api_rename_saved_review(
    review_id: str, request: ReviewRename, caller: ReviewAccess = Depends(review_caller)
):
    """Rename a saved review at the requested revision."""
    db = caller.db
    try:
        review = caller.run(review_id, RENAME, rename_saved_review, request)
    except ValueError as exc:
        db.rollback()
        raise root._review_mutation_error(exc) from exc
    if review is None:
        raise root._review_http_error("review_not_found", "Review not found", 404)
    return caller.present(review)


@router.post(
    "/api/reviews/{review_id}/duplicate",
    response_model=SavedReview,
    status_code=201,
    responses=_review_error_responses(404, 409, 502),
)
def api_duplicate_saved_review(
    review_id: str, request: ReviewDuplicate, caller: ReviewAccess = Depends(review_caller)
):
    """Create an independent copy of a saved review at the requested revision."""
    db = caller.db
    try:
        review = run_review(caller, review_id, COPY, duplicate_saved_review, request)
    except ValueError as exc:
        db.rollback()
        raise root._review_mutation_error(exc) from exc
    if review is None:
        raise root._review_http_error("review_not_found", "Review not found", 404)
    return caller.present(review)


@router.delete(
    "/api/reviews/{review_id}",
    status_code=204,
    responses=_review_error_responses(404, 409),
)
def api_delete_saved_review(
    review_id: str, revision: int, caller: ReviewAccess = Depends(review_caller)
):
    """Delete a review that has no file the caller can see; others need /deletion."""
    from scanner.persistence.repository.review_deletion import delete_review
    from scanner.persistence.repository.review_disposition import DispositionConflict

    from .review_deletion_api import disposition_error

    try:
        delete_review(caller, review_id, revision)
    except DispositionConflict as exc:
        raise disposition_error(exc) from exc
    except ValueError as exc:
        caller.db.rollback()
        raise root._review_mutation_error(exc) from exc
    return Response(status_code=204)


@router.get(
    "/api/reviews/{review_id}/filters/{filter_id}/mute-preview",
    responses=_review_error_responses(404, 409, 502),
)
def api_saved_review_mute_preview(
    review_id: str, filter_id: str, caller: ReviewAccess = Depends(review_caller)
):
    """Render the audio preview for a saved review filter."""
    try:
        preview = run_review(
            caller,
            review_id,
            PREVIEW,
            render_saved_review_filter_preview,
            filter_id,
            preview_kind="mute",
        )
    except ValueError as exc:
        raise root._review_mutation_error(exc) from exc
    if preview is None:
        raise root._review_http_error("review_filter_not_found", "Review filter not found", 404)
    return Response(content=preview, media_type="audio/mpeg", headers={"cache-control": "no-store"})


@router.get(
    "/api/reviews/{review_id}/filters/{filter_id}/video-preview",
    responses=_review_error_responses(404, 409, 502),
)
def api_saved_review_video_preview(
    review_id: str, filter_id: str, caller: ReviewAccess = Depends(review_caller)
):
    """Render the video preview for a saved review filter."""
    try:
        preview = run_review(
            caller,
            review_id,
            PREVIEW,
            render_saved_review_filter_preview,
            filter_id,
            preview_kind="video",
        )
    except ValueError as exc:
        raise root._review_mutation_error(exc) from exc
    if preview is None:
        raise root._review_http_error("review_filter_not_found", "Review filter not found", 404)
    return Response(content=preview, media_type="video/mp4", headers={"cache-control": "no-store"})


@router.post(
    "/api/reviews/{review_id}/edl",
    response_model=ReviewExportResponse,
    responses=_review_error_responses(404, 409, 502),
)
def api_export_saved_review(
    review_id: str, request: PublicationRequest, caller: ReviewAccess = Depends(review_caller)
):
    """Publish an EDL sidecar for the requested saved review revision."""
    try:
        export = compatibility_export(caller, review_id, request, ExportFormat.EDL)
    except ReviewPolicyConflict:
        raise
    except ReviewRevisionConflict as exc:
        raise root._review_http_error("stale_review_revision", str(exc)) from exc
    except ReviewMembershipError as exc:
        raise root._review_http_error("invalid_membership", str(exc)) from exc
    except ValueError as exc:
        raise root._review_http_error(
            "edl_export_failed", "Unable to export the saved review"
        ) from exc
    if export is None:
        raise root._review_http_error("review_not_found", "Review not found", 404)
    return caller.present(export)


@router.get(
    "/api/reviews/{review_id}/mcf/preflight",
    response_model=McfPreflight,
    responses=_review_error_responses(404, 409, 502),
)
def api_preflight_saved_review_mcf(review_id: str, caller: ReviewAccess = Depends(review_caller)):
    """Report issues that must be resolved before exporting a review as MCF."""
    try:
        preflight = run_review(caller, review_id, VIEW, preflight_saved_review_mcf)
    except ReviewMembershipError as exc:
        raise root._review_http_error("invalid_membership", str(exc)) from exc
    if preflight is None:
        raise root._review_http_error("review_not_found", "Review not found", 404)
    return preflight


@router.post(
    "/api/reviews/{review_id}/mcf",
    response_model=ReviewExportResponse,
    responses=_review_error_responses(404, 409, 422, 500, 502),
)
def api_export_saved_review_mcf(
    review_id: str, request: PublicationRequest, caller: ReviewAccess = Depends(review_caller)
):
    """Publish an MCF sidecar after strict preflight validation."""
    try:
        export = compatibility_export(caller, review_id, request, ExportFormat.MCF)
    except ReviewPolicyConflict:
        raise
    except ReviewRevisionConflict as exc:
        raise root._review_http_error("stale_review_revision", str(exc)) from exc
    except ReviewMembershipError as exc:
        raise root._review_http_error("invalid_membership", str(exc)) from exc
    except IncompleteMcfReview as exc:
        filter_ids = []
        document_fields = []
        for issue in exc.preflight.issues:
            if issue.filter_id is not None and issue.filter_id not in filter_ids:
                filter_ids.append(issue.filter_id)
            if issue.filter_id is None and issue.field not in document_fields:
                document_fields.append(issue.field)
        raise root._review_http_error(
            "incomplete_mcf_review",
            str(exc),
            422,
            filter_ids=filter_ids,
            document_fields=document_fields,
        ) from exc
    except ValueError as exc:
        raise root._review_http_error(
            "mcf_export_failed", "Unable to export the saved review", 500
        ) from exc
    if export is None:
        raise root._review_http_error("review_not_found", "Review not found", 404)
    return caller.present(export)
