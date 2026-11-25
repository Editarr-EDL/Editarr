"""Update checks and the daily sync (local API §8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.integrations.community.master_sync import MasterSync
from scanner.integrations.community.masters import MasterSets
from scanner.persistence.repository.community_master_links import get_link
from scanner.persistence.repository.community_master_sync import (
    mark_notifications_read,
    sync_status,
)
from scanner.persistence.write_lock import write_lock
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

from .community_master_access import (
    authorized_location,
    link_client,
    require_capability,
    respond,
    respond_async,
)
from .community_master_cooldown import cooldowns
from .community_master_json import sync_json
from .community_master_models import Empty, LinkId, NotificationsRead

router = APIRouter(prefix="/api/community", tags=["community"])


def _write(db: Session, operation):
    db.rollback()
    with write_lock(db):
        result = operation()
        db.commit()
    return result


@router.post("/masters/{link_id}/check")
async def check(
    link_id: LinkId,
    _body: Empty,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Download and apply a newer revision without using the daily sync allowance."""

    async def action():
        client = link_client(db, access, link_id, C.COMMUNITY_DOWNLOAD)
        cooldowns.claim((access.principal.user_id, "check", link_id))
        return await MasterSets(client, access.principal.user_id).check(link_id)

    return await respond_async(action)


def _service_url() -> str:
    from .community_identity_api import service_transport

    return service_transport().endpoint.url


def _locator(db: Session, access: PrincipalAccess):
    """A notification's visible location, or None when the caller may not open it."""
    owner = access.principal.user_id

    def locate(link_id: str):
        link = get_link(db, owner, link_id)
        if link is None:
            return None
        try:
            return authorized_location(db, access, link.media_version_id, C.COMMUNITY_DOWNLOAD)
        except AccessDenied as error:
            if error.code != AccessErrorCode.NOT_FOUND:
                raise
            return None

    return locate


def _status(db: Session, access: PrincipalAccess, *, ran: bool, status=None) -> dict:
    status = status or sync_status(db, access.principal.user_id, _service_url())
    result = sync_json(status, ran=ran, locate=_locator(db, access))
    db.rollback()
    return result


@router.get("/sync")
def read_sync(db: Session = Depends(get_db), access: PrincipalAccess = Depends(request_access)):
    """The account's sync state and its newest notifications."""
    require_capability(access, C.COMMUNITY_DOWNLOAD)
    return respond(lambda: _status(db, access, ran=False))


@router.post("/sync/run")
async def run_sync(
    _body: Empty,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Run the daily sync when due; otherwise report the status with `ran: false`."""
    require_capability(access, C.COMMUNITY_DOWNLOAD)

    async def action():
        from .community_identity_api import local_client

        locate = _locator(db, access)
        sync = MasterSync(
            local_client(db, access),
            access.principal.user_id,
            visible=lambda link: locate(link.id) is not None,
        )
        outcome = await sync.run()
        return _status(db, access, ran=outcome.ran, status=outcome.status)

    return await respond_async(action)


@router.post("/sync/notifications/read")
def read_notifications(
    body: NotificationsRead,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Mark the account's notifications read; `204`."""
    require_capability(access, C.COMMUNITY_DOWNLOAD)

    def action():
        _write(
            db,
            lambda: mark_notifications_read(
                db,
                access.principal.user_id,
                body.notification_ids,
                all_=bool(body.all),
            ),
        )
        return None

    return respond(action)
