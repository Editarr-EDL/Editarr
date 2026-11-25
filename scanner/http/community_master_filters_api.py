"""Master-set preferences (local API §5); filter decisions are made in reviews."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.persistence.repository.community_master_filters import (
    get_preferences,
    set_preferences,
)
from scanner.persistence.write_lock import write_lock
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

from .community_master_access import require_capability, respond
from .community_master_json import preferences_json
from .community_master_models import PreferencesRequest

router = APIRouter(prefix="/api/community", tags=["community"])


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
    """Save any preference. The threshold and Low Confidence toggle decide which community
    filters start enabled when a review includes or refreshes them; hiding downvoted
    rows affects only the list."""
    require_capability(access, C.COMMUNITY_DOWNLOAD)
    db.rollback()
    with write_lock(db):
        saved = set_preferences(
            db,
            access.principal.user_id,
            confidence_threshold=body.confidence_threshold,
            show_low_confidence=body.show_low_confidence,
            hide_rejected_downvoted=body.hide_rejected_downvoted,
        )
        db.commit()
    return respond(lambda: {"version": 1, **preferences_json(saved)})
