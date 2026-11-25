"""Find, download and view community master sets (local API §2–§3).

Master sets are per account: each route reads and writes only the caller's links,
and another account's link is `404`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from scanner.db import MediaItemRow, get_db
from scanner.formats.edl import export_edl_content
from scanner.integrations.community.find_targets import capture_find_keys
from scanner.integrations.community.master_service import MasterService
from scanner.integrations.community.masters import MasterSets
from scanner.persistence.repository.community_master_filters import master_view
from scanner.persistence.repository.community_master_links import get_link, list_links
from scanner.persistence.repository.community_master_sources import (
    as_findings,
    link_active_filters,
)
from scanner.persistence.repository.review_access import ReviewAccess, not_found
from scanner.persistence.rows.db_media_identity import MediaVersionLocatorRow
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

from . import community_master_targets as targets
from .community_master_access import (
    authorize_link,
    bound_client,
    location_media,
    media_allowed,
    require_capability,
    require_confirmed,
    respond,
    respond_async,
)
from .community_master_cooldown import cooldowns
from .community_master_json import filter_json, link_json, preferences_json
from .community_master_models import ApplyRequest, FindRequest, LinkId

router = APIRouter(prefix="/api/community", tags=["community"])


@router.post("/find")
async def find(
    body: FindRequest,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Match the target's keys against the service and remember them under a `findKey`."""

    async def action():
        target = await run_in_threadpool(
            targets.resolve_target, db, access, body.target, C.COMMUNITY_SEARCH
        )
        target_key = body.target.model_dump(mode="json")
        label_key = body.label.model_dump_json() if body.label is not None else None
        cooldowns.claim((access.principal.user_id, "find", str(target_key), label_key))
        client = bound_client(db, access, target.media, C.COMMUNITY_SEARCH)
        captured = await run_in_threadpool(capture_find_keys, db, body.target)
        version_id = await run_in_threadpool(targets.local_version, db, target, captured.sampled)
        label = body.label.model_dump() if body.label is not None else None
        return await MasterSets(client, access.principal.user_id).find(
            captured, version_id, label=label, guard=target.guard
        )

    return await respond_async(action)


@router.get("/aliases")
async def aliases(db: Session = Depends(get_db), access: PrincipalAccess = Depends(request_access)):
    """The approved aliases for the version picker: Theatrical first, Other last."""
    require_capability(access, C.COMMUNITY_SEARCH)

    async def action():
        from .community_identity_api import local_client

        approved = await MasterService(local_client(db, access)).cached_aliases()
        return {
            "version": 1,
            "choices": [
                *({"kind": "alias", "aliasId": a.alias_id, "label": a.label} for a in approved),
                {"kind": "other", "label": "Other"},
            ],
        }

    return await respond_async(action)


@router.post("/masters")
async def apply_master(
    body: ApplyRequest,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Download, verify and link a found version to the target's local version."""

    async def action():
        require_capability(access, C.COMMUNITY_DOWNLOAD)
        require_confirmed(body.confirmed)
        target = await run_in_threadpool(
            targets.resolve_target, db, access, body.target, C.COMMUNITY_DOWNLOAD
        )
        client = bound_client(db, access, target.media, C.COMMUNITY_DOWNLOAD)
        owner = access.principal.user_id
        link = await MasterSets(client, owner).download(
            find_key=body.find_key,
            community_version_id=body.community_version_id,
            replace=body.replace,
            guard=target.guard,
        )
        result = {"version": 1, "link": link_json(db, get_link(db, owner, link.id))}
        db.rollback()
        return result

    return await respond_async(action)


def _item_versions(db: Session, access: PrincipalAccess, item_id: str, media_source_id):
    """The local versions at a visible location of the item version, or `404`."""
    reviews = ReviewAccess(db, access)
    rows = db.execute(
        select(MediaVersionLocatorRow, MediaItemRow)
        .join(MediaItemRow, MediaItemRow.id == MediaVersionLocatorRow.media_item_id)
        .where(
            MediaItemRow.jellyfin_id == item_id,
            MediaVersionLocatorRow.media_source_id == (media_source_id or ""),
        )
    ).all()
    versions = {
        locator.version_id
        for locator, item in rows
        if media_allowed(
            access, reviews.media_decision(location_media(locator, item)), C.COMMUNITY_SEARCH
        )
    }
    if rows and not versions:
        raise not_found()
    return versions


@router.get("/masters")
def list_masters(
    item_id: str = Query(min_length=1, max_length=4096),
    media_source_id: str | None = Query(default=None, max_length=128),
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """The caller's links for one item version."""
    require_capability(access, C.COMMUNITY_SEARCH)

    def action():
        versions = _item_versions(db, access, item_id, media_source_id)
        links = list_links(db, access.principal.user_id, versions) if versions else []
        result = {"version": 1, "links": [link_json(db, link) for link in links]}
        db.rollback()
        return result

    return respond(action)


@router.get("/masters/{link_id}")
def view_master(
    link_id: LinkId,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """The master view: the link, the saved preferences and every row in the local set."""

    def action():
        authorize_link(db, access, link_id, C.COMMUNITY_SEARCH)
        view = master_view(db, access.principal.user_id, link_id)
        result = {
            "version": 1,
            "link": link_json(db, view.link),
            "preferences": preferences_json(view.preferences),
            "filters": [filter_json(row) for row in view.filters],
        }
        db.rollback()
        return result

    return respond(action)


@router.get("/masters/{link_id}/active-filters")
def active_filters(
    link_id: LinkId,
    clipped: bool = Query(default=True),
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """The link's filters that start enabled in a review, on the local timeline now,
    and their merged Simple EDL; `clipped=false` returns them unclipped."""

    def action():
        link = authorize_link(db, access, link_id, C.COMMUNITY_SEARCH)
        filters = link_active_filters(db, access.principal.user_id, link, clip=clipped)
        result = {
            "version": 1,
            "linkId": link.id,
            "localVersionId": link.media_version_id,
            "filters": [item.as_json() for item in filters],
            "edl": export_edl_content(as_findings(filters, scan_id="community-master")),
        }
        db.rollback()
        return result

    return respond(action)
