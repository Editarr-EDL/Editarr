"""Retired revision votes; receipt-bound master filter votes replace them (ADR 0002)."""

from fastapi import APIRouter, Depends

from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

from .community_retired import protocol_retired

router = APIRouter(prefix="/api/community/votes", tags=["community"])


@router.get("/{revision_id}")
async def feedback(revision_id: str, _access: PrincipalAccess = Depends(request_access)):
    return protocol_retired()


@router.post("/{revision_id}")
async def change_vote(revision_id: str, _access: PrincipalAccess = Depends(request_access)):
    return protocol_retired()


@router.post("/{revision_id}/retry")
async def retry_vote(revision_id: str, _access: PrincipalAccess = Depends(request_access)):
    return protocol_retired()
