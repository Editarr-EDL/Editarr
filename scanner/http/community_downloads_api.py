"""Retired revision downloads; master-set download and apply replaces them (ADR 0002)."""

from fastapi import APIRouter, Depends

from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

from .community_retired import protocol_retired

router = APIRouter(prefix="/api/community/downloads", tags=["community"])


@router.post("")
async def download(access: PrincipalAccess = Depends(request_access)):
    return protocol_retired(access, C.COMMUNITY_DOWNLOAD)


@router.post("/status")
async def status(access: PrincipalAccess = Depends(request_access)):
    return protocol_retired(access, C.COMMUNITY_DOWNLOAD)
