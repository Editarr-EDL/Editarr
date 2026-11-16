"""The read-only archive of media items from sources Editarr no longer supports."""

from dataclasses import asdict
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.persistence.repository.archive_records import read_archive
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

router = APIRouter(prefix="/api/archive", tags=["archive"])


class ArchivedScanOut(BaseModel):
    id: str
    scan_number: int
    name: str | None
    status: str
    created_at: datetime


class ArchivedReviewOut(BaseModel):
    id: str
    name: str
    owner: str | None
    updated_at: datetime


class ArchivedExportOut(BaseModel):
    id: int
    format: Literal["edl", "mcf"]
    scope: Literal["legacy", "default", "personal"]
    exported_at: datetime


class ArchivedItemOut(BaseModel):
    id: str
    source: Literal["radarr", "sonarr", "manual_files"]
    title: str
    item_type: str
    series: str | None
    season: int | None
    episode: int | None
    last_episode: int | None
    path: str | None
    scans: list[ArchivedScanOut]
    reviews: list[ArchivedReviewOut]
    exports: list[ArchivedExportOut]


class ArchiveOut(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[ArchivedItemOut]


@router.get("", response_model=ArchiveOut)
def archive_list(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    access: PrincipalAccess = Depends(request_access),
    db: Session = Depends(get_db),
) -> ArchiveOut:
    """One page of archived items; paths appear only for callers who may see media paths."""
    require(access, Capability.ARCHIVE_VIEW)
    page = read_archive(
        db,
        with_paths=Capability.MEDIA_PATHS_VIEW in access.capabilities,
        offset=offset,
        limit=limit,
    )
    return ArchiveOut(
        total=page.total,
        offset=offset,
        limit=limit,
        items=[ArchivedItemOut.model_validate(asdict(item)) for item in page.items],
    )
