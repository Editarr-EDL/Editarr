"""Retired revision catalog lookup; master-set Find replaces it (ADR 0002)."""

from fastapi import APIRouter, Depends

from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

from .community_retired import protocol_retired

router = APIRouter(prefix="/api/community/lookup", tags=["community"])


@router.post("")
async def lookup(_access: PrincipalAccess = Depends(request_access)):
    return protocol_retired()
