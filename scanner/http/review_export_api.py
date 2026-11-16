"""Authenticated personal downloads and recoverable export publication."""

import errno
import json
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field
from sqlalchemy.exc import SQLAlchemyError

from scanner.db import MediaItemExportRow, SavedReviewRow
from scanner.modeling.model_publications import ExportPublication, ExportPublications
from scanner.models import ExportFormat, ReviewExportRequest
from scanner.persistence.repository.export_deletion import delete_export
from scanner.persistence.repository.export_discovery import (
    discover_publications,
    publication_status,
    require_export,
)
from scanner.persistence.repository.export_publication import (
    ExportConflict,
    export_identity,
    publish_export,
)
from scanner.persistence.repository.export_recovery import ExportRecoveryRequired, publication_lock
from scanner.persistence.repository.review_access import ReviewAccess, not_found
from scanner.persistence.repository.review_export_content import prepare_review_export
from scanner.persistence.repository.review_policy import ReviewPolicyConflict
from scanner.persistence.repository.scan_jobs import ScanMedia
from scanner.persistence.rows.db_policies import ExportScope
from scanner.repository import IncompleteMcfReview, ReviewMembershipError, ReviewRevisionConflict
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.targets import AccountRef, ActionPair, ResourceRef

from .review_access import review_caller, run_review

router = APIRouter()


class PublicationRequest(ReviewExportRequest):
    target: Literal["self", "review_owner", "item_default"] = "self"
    confirmation: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def _review_http_error(*args, **kwargs):
    from .review_target_api import _review_http_error as error

    return error(*args, **kwargs)


# The export path's folders cannot be resolved; unlike ENOENT this is not absence.
_UNRESOLVABLE = frozenset({errno.ELOOP, errno.ENOTDIR})


def _error(exc):
    if isinstance(exc, OSError) and exc.errno in _UNRESOLVABLE:
        return _review_http_error(
            "export_destination_unavailable",
            "The export folder cannot be reached; restore the media folder and retry",
            409,
        )
    if isinstance(exc, ExportConflict):
        extra = {"confirmation": exc.confirmation} if exc.confirmation else {}
        return HTTPException(
            409,
            detail={"code": exc.code, "message": str(exc), **extra},
            headers={"cache-control": "no-store"},
        )
    if isinstance(exc, ExportRecoveryRequired):
        return _review_http_error("export_recovery_required", "Export recovery is required", 409)
    if isinstance(exc, ReviewRevisionConflict):
        return _review_http_error("stale_review_revision", str(exc), 409)
    if isinstance(exc, ReviewMembershipError):
        return _review_http_error("invalid_membership", str(exc), 409)
    if isinstance(exc, IncompleteMcfReview):
        return _review_http_error(
            "incomplete_mcf_review",
            str(exc),
            422,
            filter_ids=list(
                dict.fromkeys(
                    issue.filter_id for issue in exc.preflight.issues if issue.filter_id is not None
                )
            ),
            document_fields=list(
                dict.fromkeys(
                    issue.field for issue in exc.preflight.issues if issue.filter_id is None
                )
            ),
        )
    return _review_http_error("export_write_failed", "Unable to update the export", 500)


def publish(
    caller: ReviewAccess, review_id: str, request: PublicationRequest, format: ExportFormat
):
    """Bind target permissions and the current principal to the locked publication."""
    own = C.REVIEWS_VIEW_OWN if request.target == "item_default" else C.EXPORTS_PERSONAL_PUBLISH_OWN
    action = ActionPair(own, C.REVIEWS_EXPORT_OTHER)
    extra = (C.EXPORTS_DEFAULT_PUBLISH,) if request.target == "item_default" else ()

    def operation(db, source_id):
        if request.target == "self":
            facts = caller.visible(source_id)
            ref = caller.ref(facts)
            require(
                caller.access,
                C.EXPORTS_PERSONAL_PUBLISH_OWN,
                ResourceRef(
                    AccountRef(
                        caller.access.principal.user_id, caller.access.principal.account.role
                    ),
                    ref.media,
                ),
            )
        return publish_export(
            db,
            source_id,
            request.revision,
            format,
            access=caller.access,
            target=request.target,
            confirmation=request.confirmation,
            policy_revision=request.policy_revision,
            policy_owner_id=request.policy_owner_id,
        )

    try:
        with publication_lock(caller.db):
            return run_review(caller, review_id, action, operation, extra_capabilities=extra)
    except ReviewPolicyConflict:
        raise
    except (ValueError, OSError, SQLAlchemyError) as exc:
        raise _error(exc) from exc


@router.post("/api/reviews/{review_id}/exports/{format}", response_model=ExportPublication)
def api_publish_review_export(
    review_id: str,
    format: ExportFormat,
    request: PublicationRequest,
    caller: ReviewAccess = Depends(review_caller),
):
    result = publish(caller, review_id, request, format)
    return caller.present(result["publication"])


def compatibility_export(caller, review_id, request, format):
    """Keep existing review response fields while publishing only the canonical layout."""
    result = publish(caller, review_id, request, format)
    return result["compatibility"]


@router.post("/api/reviews/{review_id}/exports/{format}/download")
def api_download_review_export(
    review_id: str,
    format: ExportFormat,
    request: ReviewExportRequest,
    caller: ReviewAccess = Depends(review_caller),
):
    """Download the same prepared bytes without creating directories or export rows."""

    def operation(db, source_id):
        ref = caller.ref(caller.visible(source_id))
        require(
            caller.access,
            C.EXPORTS_PERSONAL_DOWNLOAD_OWN,
            ResourceRef(
                AccountRef(caller.access.principal.user_id, caller.access.principal.account.role),
                ref.media,
            ),
        )
        output = prepare_review_export(
            db,
            source_id,
            request.revision,
            format,
            policy_revision=request.policy_revision,
            policy_owner_id=request.policy_owner_id,
        )
        from scanner.formats.export_layout import personal_export_path

        identity = export_identity(db, int(caller.access.principal.user_id))
        filename = personal_export_path(output.media, identity, format).name
        return output.content, filename

    action = ActionPair(C.EXPORTS_PERSONAL_DOWNLOAD_OWN, C.REVIEWS_EXPORT_OTHER)
    try:
        content, filename = run_review(caller, review_id, action, operation)
    except ReviewPolicyConflict:
        raise
    except (ValueError, OSError, SQLAlchemyError) as exc:
        raise _error(exc) from exc
    return Response(
        content=content,
        media_type="text/vtt" if format == ExportFormat.MCF else "text/plain",
        headers={
            "cache-control": "private, no-store",
            "content-disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        },
    )


def _export(caller, export_id, *, deleting=False, revision=None, publication_id=None):
    row = caller.db.get(MediaItemExportRow, export_id)
    if row is None or row.scope == ExportScope.LEGACY or row.snapshot_json is None:
        raise not_found()
    snapshot = json.loads(row.snapshot_json)
    media = ScanMedia(
        row.media_item.server_id,
        row.media_item.jellyfin_id,
        row.media_item.library_id,
        snapshot["media_path"],
    )
    captured_revision = row.publication_revision
    with caller.writing([media]) as evidence:
        row = caller.db.get(MediaItemExportRow, export_id)
        if (
            row is None
            or row.snapshot_json is None
            or row.publication_revision != captured_revision
            or json.loads(row.snapshot_json).get("publication_id") != snapshot["publication_id"]
        ):
            raise _review_http_error("stale_export_revision", "The export changed; reload it", 409)
        allowed = caller.media_decision(media, evidence=evidence[0])
        require_export(caller, row, allowed, deleting=deleting)
        if deleting:
            delete_export(
                caller.db, row, revision, caller.access, publication_id=str(publication_id)
            )
            return None
        return publication_status(row, caller.access.principal.user_id)


@router.get("/api/reviews/{review_id}/exports", response_model=ExportPublications)
def api_list_review_exports(review_id: str, caller: ReviewAccess = Depends(review_caller)):
    caller.visible(review_id)
    review = caller.db.get(SavedReviewRow, review_id)
    return {
        "publications": [
            result.publication
            for result in discover_publications(
                caller,
                review.media_item_id,
                media_source_id=review.media_source_id,
                review_id=review_id,
            )
        ]
    }


@router.get("/api/exports", response_model=ExportPublications)
def api_list_version_exports(
    media_item_id: str,
    media_source_id: str = "",
    caller: ReviewAccess = Depends(review_caller),
):
    return {
        "publications": [
            result.publication
            for result in discover_publications(
                caller,
                media_item_id,
                media_source_id=media_source_id,
            )
        ]
    }


@router.get("/api/exports/{export_id}", response_model=ExportPublication)
def api_get_export(export_id: int, caller: ReviewAccess = Depends(review_caller)):
    return caller.present(_export(caller, export_id))


@router.delete("/api/exports/{export_id}", status_code=204)
def api_delete_export(
    export_id: int,
    revision: int,
    publication_id: UUID,
    caller: ReviewAccess = Depends(review_caller),
):
    try:
        with publication_lock(caller.db):
            _export(
                caller, export_id, deleting=True, revision=revision, publication_id=publication_id
            )
    except (ExportConflict, ExportRecoveryRequired, OSError, SQLAlchemyError) as exc:
        raise _error(exc) from exc
    return Response(status_code=204)
