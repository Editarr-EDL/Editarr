"""Update checks, update review and the daily sync (local API §8–§9)."""

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
from scanner.persistence.repository.community_master_updates import (
    accept_all,
    decide_updates,
    master_update,
)
from scanner.persistence.write_lock import write_lock
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

from .community_master_access import (
    authorize_link,
    authorized_location,
    link_client,
    require_capability,
    respond,
    respond_async,
)
from .community_master_json import sync_json, update_json
from .community_master_models import AcceptAll, Empty, NotificationsRead, UpdateDecisions

router = APIRouter(prefix="/api/community", tags=["community"])


@router.post("/masters/{link_id}/check")
async def check(
    link_id: str,
    _body: Empty,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Store a newer revision for review without applying it or using the daily sync."""

    async def action():
        client = link_client(db, access, link_id, C.COMMUNITY_DOWNLOAD)
        return await MasterSets(client, access.principal.user_id).check(link_id)

    return await respond_async(action)


@router.get("/masters/{link_id}/update")
def read_update(
    link_id: str,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """The undecided rows of the latest revision, grouped for review."""

    def action():
        authorize_link(db, access, link_id, C.COMMUNITY_DOWNLOAD)
        result = update_json(master_update(db, access.principal.user_id, link_id))
        db.rollback()
        return result

    return respond(action)


def _write(db: Session, operation):
    db.rollback()
    with write_lock(db):
        result = operation()
        db.commit()
    return result


@router.post("/masters/{link_id}/update/decisions")
def decide(
    link_id: str,
    body: UpdateDecisions,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Apply decisions atomically; `204` and the client re-reads the update."""

    def action():
        authorize_link(db, access, link_id, C.COMMUNITY_DOWNLOAD)
        pairs = [(item.row_id, item.decision) for item in body.decisions]
        _write(
            db,
            lambda: decide_updates(db, access.principal.user_id, link_id, body.to_revision, pairs),
        )
        return None

    return respond(action)


@router.post("/masters/{link_id}/update/accept-all")
def accept_group(
    link_id: str,
    body: AcceptAll,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Accept every untouched, acceptable row in a group; returns the remaining update."""

    def action():
        authorize_link(db, access, link_id, C.COMMUNITY_DOWNLOAD)
        view = _write(
            db,
            lambda: accept_all(db, access.principal.user_id, link_id, body.to_revision, body.group),
        )
        return update_json(view)

    return respond(action)


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

        outcome = await MasterSync(local_client(db, access), access.principal.user_id).run()
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
