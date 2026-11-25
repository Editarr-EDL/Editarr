"""Receipt-bound filter votes and reports on the caller's master links (local API §6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.integrations.community.master_votes import MasterFeedback
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

from .community_master_access import (
    link_client,
    require_capability,
    require_confirmed,
    respond_async,
)
from .community_master_models import FilterId, LinkId, ReportRequest, VoteRequest, VoteRetry

router = APIRouter(prefix="/api/community/masters/{link_id}", tags=["community"])


def _feedback(db: Session, access: PrincipalAccess, link_id: str) -> MasterFeedback:
    client = link_client(db, access, link_id, C.COMMUNITY_FEEDBACK_SUBMIT)
    return MasterFeedback(client, access.principal.user_id)


@router.post("/filters/{filter_id}/vote")
async def vote(
    link_id: LinkId,
    filter_id: FilterId,
    body: VoteRequest,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Persist the vote intent, send it with the link's receipt and record `myVote`."""

    async def action():
        require_capability(access, C.COMMUNITY_FEEDBACK_SUBMIT)
        require_confirmed(body.confirmed)
        feedback = _feedback(db, access, link_id)
        return await feedback.vote(link_id, filter_id, body.vote, body.operation_id)

    return await respond_async(action)


@router.post("/filters/{filter_id}/vote/retry")
async def retry_vote(
    link_id: LinkId,
    filter_id: FilterId,
    body: VoteRetry,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Resend a saved vote intent's exact bytes."""

    async def action():
        require_capability(access, C.COMMUNITY_FEEDBACK_SUBMIT)
        require_confirmed(body.confirmed)
        feedback = _feedback(db, access, link_id)
        return await feedback.send_vote(link_id, filter_id, body.operation_id)

    return await respond_async(action)


@router.post("/reports")
async def report(
    link_id: LinkId,
    body: ReportRequest,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Report the link's version or one of its served filters to moderators."""

    async def action():
        require_capability(access, C.COMMUNITY_FEEDBACK_SUBMIT)
        require_confirmed(body.confirmed)
        feedback = _feedback(db, access, link_id)
        return await feedback.report(
            link_id,
            target_type=body.target.type,
            target_id=body.target.id,
            reason=body.reason,
            note=(body.note or "").strip() or None,
        )

    return await respond_async(action)
