"""Local filter decisions and display preferences (local API §3, §5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from scanner.db import CommunityMasterFilterRow, get_db
from scanner.modeling.model_core import MCF_CATEGORY_TO_FAMILY
from scanner.persistence.repository.community_master_filters import (
    get_preferences,
    set_filter_decision,
    set_preferences,
)
from scanner.persistence.repository.review_access import not_found
from scanner.persistence.write_lock import write_lock
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

from .community_master_access import authorize_link, invalid, require_capability, respond
from .community_master_json import filter_json, preferences_json
from .community_master_models import FilterDecisionRequest, FilterId, LinkId, PreferencesRequest

router = APIRouter(prefix="/api/community", tags=["community"])


def _check_values(body: FilterDecisionRequest, category: str) -> dict:
    """Value fields are only for `adjust`, which needs one; a subcategory must belong to
    the row's top-level category."""
    values = body.values()
    if body.decision != "adjust":
        if values:
            raise invalid("decision", "Only adjust takes value fields")
        return values
    if not values:
        raise invalid("decision", "adjust requires at least one value field")
    if "subcategory" in values and MCF_CATEGORY_TO_FAMILY.get(values["subcategory"]) != category:
        raise invalid("subcategory", "The subcategory does not belong to the filter's category")
    return values


@router.patch("/masters/{link_id}/filters/{filter_id}")
def decide_filter(
    link_id: LinkId,
    filter_id: FilterId,
    body: FilterDecisionRequest,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Verify, reject, adjust or reset one row; returns the updated filter."""

    def action():
        link = authorize_link(db, access, link_id, C.COMMUNITY_DOWNLOAD)
        row = db.get(CommunityMasterFilterRow, (link.id, filter_id))
        if row is None:
            raise not_found()
        values = _check_values(body, row.category)
        db.rollback()
        with write_lock(db):
            try:
                view = set_filter_decision(
                    db,
                    access.principal.user_id,
                    link_id,
                    filter_id,
                    body.decision,
                    values,
                    expected_state=body.expected_state,
                )
            except ValueError as error:
                raise invalid("start_ms", str(error)) from error
            db.commit()
        return {"version": 1, **filter_json(view)}

    return respond(action)


@router.get("/preferences")
def read_preferences(
    db: Session = Depends(get_db), access: PrincipalAccess = Depends(request_access)
):
    """The account's master-set display preferences, or the defaults."""
    require_capability(access, C.COMMUNITY_SEARCH)
    preferences = get_preferences(db, access.principal.user_id)
    db.rollback()
    return respond(lambda: {"version": 1, **preferences_json(preferences)})


@router.patch("/preferences")
def save_preferences(
    body: PreferencesRequest,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Save either preference; they decide which untouched rows are active."""
    require_capability(access, C.COMMUNITY_DOWNLOAD)
    db.rollback()
    with write_lock(db):
        saved = set_preferences(
            db,
            access.principal.user_id,
            confidence_threshold=body.confidence_threshold,
            show_low_confidence=body.show_low_confidence,
        )
        db.commit()
    return respond(lambda: {"version": 1, **preferences_json(saved)})
