"""Protected local vote intents and private current feedback reads."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from scanner.db import get_db
from scanner.integrations.community.votes import VoteOutbox
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

from .community_activity_access import vote_client

router = APIRouter(prefix="/api/community/votes", tags=["community"])


class VoteChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    vote: Literal["like", "dislike", "neutral"]
    operation_id: str = Field(pattern=r"^vote-[A-Za-z0-9_.-]{1,120}$")
    confirmed: bool


class VoteRetry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(pattern=r"^vote-[A-Za-z0-9_.-]{1,120}$")
    confirmed: bool


@router.get("/{revision_id}")
async def feedback(
    revision_id: str,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    from .community_api import _result

    return await _result(lambda: VoteOutbox(vote_client(db, access)).read(revision_id))


@router.post("/{revision_id}")
async def change_vote(
    revision_id: str,
    body: VoteChange,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    from .community_api import _result

    if not body.confirmed:
        return JSONResponse(
            {"code": "confirmation_required", "retryable": False},
            status_code=409,
            headers={"Cache-Control": "no-store"},
        )

    async def send():
        outbox = VoteOutbox(vote_client(db, access))
        outbox.prepare(revision_id, body.vote, body.operation_id)
        return await outbox.run(revision_id, body.operation_id)

    return await _result(send)


@router.post("/{revision_id}/retry")
async def retry_vote(
    revision_id: str,
    body: VoteRetry,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    from .community_api import _result

    if not body.confirmed:
        return JSONResponse(
            {"code": "confirmation_required", "retryable": False},
            status_code=409,
            headers={"Cache-Control": "no-store"},
        )
    return await _result(
        lambda: VoteOutbox(vote_client(db, access)).run(revision_id, body.operation_id)
    )
