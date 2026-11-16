"""How far an account's latest link has removed its old personal export files.

A read for the account DTO: counts only, never paths, digests, or proof material.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from scanner.persistence.rows.db_account_links import (
    AccountLinkExportMoveRow,
    AccountLinkTransitionRow,
    TransitionState,
)

_SWITCHED = (TransitionState.REMOVING.value, TransitionState.COMPLETED.value)


class LinkCleanup(BaseModel):
    """Old-file outcomes of the account's latest link that switched its identity.

    `pending` old files are not settled yet; recovery retries them at startup and
    hourly while `state` is `removing`. `kept` files held bytes Editarr did not write
    and were left in place; `absent` files were already gone.
    """

    state: Literal["removing", "completed"]
    pending: int
    removed: int
    absent: int
    kept: int


def read(db: Session, user_id: int) -> LinkCleanup | None:
    transition = (
        db.query(AccountLinkTransitionRow)
        .filter(
            AccountLinkTransitionRow.user_id == user_id,
            AccountLinkTransitionRow.state.in_(_SWITCHED),
        )
        .order_by(AccountLinkTransitionRow.id.desc())
        .first()
    )
    if transition is None:
        return None
    outcomes = dict(
        db.query(AccountLinkExportMoveRow.outcome, func.count())
        .filter(AccountLinkExportMoveRow.transition_id == transition.id)
        .group_by(AccountLinkExportMoveRow.outcome)
        .all()
    )
    return LinkCleanup(
        state=transition.state,
        pending=outcomes.get(None, 0),
        removed=outcomes.get("removed", 0),
        absent=outcomes.get("absent", 0),
        kept=outcomes.get("kept", 0),
    )
