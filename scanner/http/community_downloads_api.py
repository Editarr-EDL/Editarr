"""Protected one-item downloads and local committed-status reconciliation."""

import asyncio
from threading import Event

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.formats.community.codec import ResourceBounds
from scanner.integrations.community.activity_access import ActivityAccess
from scanner.integrations.community.local_downloads import (
    CommunityDownloads,
    download_status,
    media_row,
)
from scanner.integrations.community.lookup_targets import MissingTarget
from scanner.integrations.community.transport import CommunityFailure
from scanner.modeling.model_community_downloads import DownloadRequest, DownloadStatusRequest
from scanner.persistence.repository.review_access import ReviewAccess, media_facts, not_found
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access
from scanner.security.access.vocabulary import Role
from scanner.settings.config import settings

from .community_activity_access import target_client

router = APIRouter(prefix="/api/community/downloads", tags=["community"])


def download_service(db, access):

    if not settings.community_download_bounds or not settings.community_download_response_bytes:
        raise CommunityFailure("downloads_not_configured")
    try:
        bounds = ResourceBounds(**settings.community_download_bounds)
    except (TypeError, ValueError) as error:
        raise CommunityFailure("downloads_not_configured") from error
    return CommunityDownloads(
        target_client(db, access, C.COMMUNITY_DOWNLOAD),
        bounds=bounds,
        response_bytes=settings.community_download_response_bytes,
    )


def authorize_status(db, access, target):
    """Local status requires current media authority but never provider or service access."""
    try:
        media = media_row(db, target)
    except MissingTarget:
        if access.principal.account.role is not Role.OWNER:
            raise not_found() from None
        raise
    ActivityAccess(ReviewAccess(db, access), media_facts(db, media), C.COMMUNITY_DOWNLOAD).check()


@router.post("")
async def download(
    body: DownloadRequest,
    request: Request,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    from .community_api import _result

    cancelled = Event()

    async def action():
        try:
            return await download_service(db, access).run(body, should_cancel=cancelled.is_set)
        except MissingTarget:
            if access.principal.account.role is not Role.OWNER:
                raise not_found() from None
            raise

    operation = asyncio.create_task(_result(action))

    async def disconnected():
        while not operation.done():
            if await request.is_disconnected():
                cancelled.set()
                operation.cancel()
                return
            await asyncio.sleep(0.1)

    watcher = asyncio.create_task(disconnected())
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        cancelled.set()
        if not operation.done() and not operation.cancelling():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        raise
    finally:
        cancelled.set()
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


@router.post("/status")
async def status(
    body: DownloadStatusRequest,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    from .community_api import _result

    def action():
        authorize_status(db, access, body.target)
        return download_status(
            db, settings.community_service_url, body, user_id=int(access.principal.user_id)
        )

    return await _result(action)
