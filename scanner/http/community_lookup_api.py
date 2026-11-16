"""Administrator/CSRF protected, cancellable one-target catalog lookup."""

import asyncio
from threading import Event

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.integrations.community.lookup import CommunityLookup
from scanner.integrations.community.lookup_targets import MissingTarget
from scanner.modeling.model_community_lookup import LookupRequest
from scanner.persistence.repository.review_access import not_found
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access
from scanner.security.access.vocabulary import Role

from .community_activity_access import target_client

router = APIRouter(prefix="/api/community/lookup", tags=["community"])


@router.post("")
async def lookup(
    body: LookupRequest,
    request: Request,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    from .community_api import _result

    cancelled = Event()

    async def action():
        try:
            return await CommunityLookup(target_client(db, access, C.COMMUNITY_SEARCH)).run(
                body, should_cancel=cancelled.is_set
            )
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
