"""Session- and CSRF-protected local community activities on the caller's own identity.

Connection, enrollment, profile, and key routes live in `community_identity_api`.
The routes here act through the caller's identity; MU-29 scopes them by actor.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from scanner.db import get_db
from scanner.integrations.community.activity_access import CHECK, RETRACT, RETRY
from scanner.integrations.community.publications import PublicationOutbox
from scanner.integrations.community.transport import CommunityFailure
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access
from scanner.security.private_identity_files import IdentityUnavailable
from scanner.settings.config import settings

from .community_activity_access import publication_client
from .community_downloads_api import router as downloads_router
from .community_identity_api import RotationConfirmation
from .community_identity_api import router as identity_router
from .community_identity_api import users_router as identity_users_router
from .community_lookup_api import router as lookup_router
from .community_master_alignment_api import router as master_alignment_router
from .community_master_feedback_api import router as master_feedback_router
from .community_master_filters_api import router as master_filters_router
from .community_master_updates_api import router as master_updates_router
from .community_masters_api import router as masters_router
from .community_retired import protocol_retired
from .community_sharing_api import router as sharing_router
from .community_votes_api import router as votes_router

router = APIRouter(prefix="/api/community", tags=["community"])


async def _result(operation):
    try:
        result = operation()
        if hasattr(result, "__await__"):
            result = await result
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except CommunityFailure as error:
        if error.code in {"preview_expired", "preview_unavailable", "vote_intent_unavailable"}:
            from scanner.persistence.repository.review_access import not_found

            raise not_found() from error
        return JSONResponse(
            {"code": error.code, "retryable": error.retryable},
            status_code=503 if error.retryable or error.code == "not_configured" else 409,
            headers={"Cache-Control": "no-store"},
        )
    except IdentityUnavailable as error:
        return JSONResponse(
            {"code": "identity_" + error.status, "retryable": False},
            status_code=409,
            headers={"Cache-Control": "no-store"},
        )


def publication_outbox(
    db: Session, access: PrincipalAccess, operation_id: str, action
) -> PublicationOutbox:
    return PublicationOutbox(
        publication_client(db, access, operation_id, action),
        request_bytes=settings.community_publication_request_bytes,
    )


@router.get("/publications/{operation_id}")
async def publication_status(
    operation_id: str,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    return await _result(
        lambda: publication_outbox(db, access, operation_id, CHECK).status(operation_id)
    )


@router.post("/publications/{operation_id}/check")
async def publication_check(
    operation_id: str,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    return await _result(
        lambda: publication_outbox(db, access, operation_id, CHECK).run(operation_id)
    )


@router.post("/publications/{operation_id}/retry")
async def publication_retry(
    operation_id: str,
    body: RotationConfirmation,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    if not body.confirmed:
        return JSONResponse({"code": "confirmation_required", "retryable": False}, status_code=409)
    return await _result(
        lambda: publication_outbox(db, access, operation_id, RETRY).run(operation_id, resend=True)
    )


@router.delete("/publications/{operation_id}")
async def publication_cancel(
    operation_id: str,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    return await _result(
        lambda: publication_outbox(db, access, operation_id, RETRACT).cancel(operation_id)
    )


@router.post("/import-receipts/recover")
async def recover_receipts(_access: PrincipalAccess = Depends(request_access)):
    return protocol_retired()


@router.post("/import-receipts/{import_id}/retry")
async def retry_receipt(import_id: str, _access: PrincipalAccess = Depends(request_access)):
    return protocol_retired()


connection_router = router
router = APIRouter()
router.include_router(connection_router)
router.include_router(identity_router)
router.include_router(identity_users_router)
router.include_router(sharing_router)
router.include_router(lookup_router)
router.include_router(downloads_router)
router.include_router(votes_router)
router.include_router(masters_router)
router.include_router(master_alignment_router)
router.include_router(master_filters_router)
router.include_router(master_feedback_router)
router.include_router(master_updates_router)
