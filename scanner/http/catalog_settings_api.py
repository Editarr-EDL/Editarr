"""Connected source credentials and filter-default settings routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from scanner import api as root
from scanner.api import (
    JELLYFIN_ITEM_CACHE,
    LIBRARY_ITEMS_CACHE,
    LIBRARY_ITEMS_CACHE_LOCK,
    _review_error_responses,
)
from scanner.db import get_db
from scanner.integrations.jellyfin import JellyfinError
from scanner.integrations.jellyfin_server_url import normalize_server_url
from scanner.modeling.model_reviews import ReviewFilterDefaultsApplyResult
from scanner.models import (
    FilterDefaultsApplyRequest,
    FilterDefaultsSettings,
    JellyfinSettings,
    JellyfinSettingsUpdate,
)
from scanner.persistence.repository.filter_preferences import (
    FilterPreferenceConflict,
)
from scanner.persistence.repository.repository_settings import replace_filter_defaults
from scanner.repository import (
    FilterSettingsRevisionConflict,
    get_jellyfin_settings,
    get_structured_filter_defaults_settings,
    update_jellyfin_settings,
)
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access
from scanner.security.audit.actions import AuditAction
from scanner.settings.filter_policy.events import FilterPolicyChange
from scanner.settings.filter_policy.updates import InstanceDefaultsUpdate

from . import filter_policy_api
from .operations_access import Operator, operation

logger = logging.getLogger(__name__)
router = APIRouter()
_CHANGE = AuditAction.CONNECTION_CHANGE
_VIEW = [Depends(operation(C.CONNECTIONS_VIEW))]
_TEST = [Depends(operation(C.CONNECTIONS_TEST))]
_EDIT = operation(C.CONNECTIONS_EDIT, _CHANGE)
_DISCONNECT = operation(C.CONNECTIONS_DISCONNECT, _CHANGE)


def _test_jellyfin_connection(server_url: str, api_key: str) -> None:
    with root.JellyfinClient(server_url, api_key) as client:
        client.test_connection()


def _candidate_jellyfin_credentials(db: Session, update: JellyfinSettingsUpdate) -> tuple[str, str]:
    current = root.get_jellyfin_credentials(db)
    current_server_url, current_api_key = current or (None, None)
    server_url = update.server_url.strip() if update.server_url is not None else current_server_url
    api_key = update.api_key.strip() if update.api_key is not None else current_api_key
    if update.clear_api_key:
        api_key = None
    if not server_url or not api_key:
        raise HTTPException(400, "Enter both a Jellyfin server address and API key")
    return normalize_server_url(server_url), api_key


@router.get("/api/settings/jellyfin", response_model=JellyfinSettings, dependencies=_VIEW)
def api_get_jellyfin_settings(db: Session = Depends(get_db)):
    return get_jellyfin_settings(db)


@router.post("/api/settings/jellyfin/test", response_model=JellyfinSettings, dependencies=_TEST)
def api_test_jellyfin_settings(update: JellyfinSettingsUpdate, db: Session = Depends(get_db)):
    """Validate supplied/current connection values without persisting changes."""
    try:
        server_url, api_key = _candidate_jellyfin_credentials(db, update)
        root._test_jellyfin_connection(server_url, api_key)
    except JellyfinError as exc:
        logger.warning("Jellyfin connection test failed: %s", exc)
        raise HTTPException(400, str(exc)) from exc
    logger.info("Jellyfin connection test succeeded")
    return JellyfinSettings(
        server_url=server_url,
        api_key_configured=True,
    )


@router.put("/api/settings/jellyfin", response_model=JellyfinSettings)
def api_update_jellyfin_settings(
    update: JellyfinSettingsUpdate,
    db: Session = Depends(get_db),
    caller: Operator = Depends(_EDIT),
):
    """Test credentials before storing them, so a failed setup changes nothing."""
    if update.clear_api_key:
        if update.server_url is not None:
            try:
                update.server_url = normalize_server_url(update.server_url)
            except JellyfinError as exc:
                raise HTTPException(400, str(exc)) from exc
        caller.stage(_CHANGE, {"service": "jellyfin", "change": "saved"})
        result = update_jellyfin_settings(db, update)
        with LIBRARY_ITEMS_CACHE_LOCK:
            LIBRARY_ITEMS_CACHE.clear()
            JELLYFIN_ITEM_CACHE.clear()
        return result
    try:
        server_url, api_key = _candidate_jellyfin_credentials(db, update)
        root._test_jellyfin_connection(server_url, api_key)
    except JellyfinError as exc:
        logger.warning("Jellyfin settings were not saved because validation failed: %s", exc)
        raise HTTPException(400, str(exc)) from exc
    update.server_url = server_url
    caller.stage(_CHANGE, {"service": "jellyfin", "change": "saved"})
    result = update_jellyfin_settings(db, update)
    with LIBRARY_ITEMS_CACHE_LOCK:
        LIBRARY_ITEMS_CACHE.clear()
        JELLYFIN_ITEM_CACHE.clear()
    root.record_jellyfin_connection(db, None)
    logger.info("Jellyfin settings saved after a successful connection test")
    return result


@router.delete("/api/settings/jellyfin", response_model=JellyfinSettings)
def api_disconnect_jellyfin(db: Session = Depends(get_db), caller: Operator = Depends(_DISCONNECT)):
    caller.stage(_CHANGE, {"service": "jellyfin", "change": "disconnected"})
    result = update_jellyfin_settings(db, JellyfinSettingsUpdate(server_url="", clear_api_key=True))
    with LIBRARY_ITEMS_CACHE_LOCK:
        LIBRARY_ITEMS_CACHE.clear()
        JELLYFIN_ITEM_CACHE.clear()
    return result


@router.get("/api/settings/filters", response_model=FilterDefaultsSettings)
def api_get_filter_defaults_settings(db: Session = Depends(get_db)):
    return get_structured_filter_defaults_settings(db)


@router.put(
    "/api/settings/filters",
    response_model=FilterDefaultsSettings,
    responses=_review_error_responses(409),
)
def api_update_filter_defaults_settings(
    settings_update: InstanceDefaultsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Replace the instance defaults; users' copies, overrides, and policies follow.

    An edit that changes any user's enforced policy needs the owner's password in
    `reauthentication` and is audited per changed user (see
    `filter_policy_api.admit_instance_policy_changes`) in the edit's transaction.
    """
    require(access, C.FILTERS_INSTANCE_DEFAULTS_EDIT)

    def admit(changes: list[FilterPolicyChange]) -> None:
        filter_policy_api.admit_instance_policy_changes(
            db, access, request, settings_update.reauthentication, changes
        )

    try:
        return replace_filter_defaults(db, settings_update, stage_changes=admit)[0]
    except FilterSettingsRevisionConflict as exc:
        raise root._review_http_error("stale_settings_revision", str(exc)) from exc
    except FilterPreferenceConflict as exc:
        raise root._review_http_error(exc.code, exc.detail) from exc


@router.post("/api/settings/filters/apply", response_model=ReviewFilterDefaultsApplyResult)
def api_apply_filter_defaults(
    request: FilterDefaultsApplyRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Apply the caller's effective defaults, including any enforced policy, to draft copies.

    The body is saved content (or a draft of it), never the enforced review view.
    """
    from scanner.persistence.repository.review_access import ReviewAccess
    from scanner.persistence.repository.review_policy_apply import (
        apply_new_defaults,
        apply_review_defaults,
    )
    from scanner.security.access.targets import ActionPair

    if request.review_id is not None:
        from .review_access import run_review

        return run_review(
            ReviewAccess(db, access, http_request.state.access_credential.session_id),
            request.review_id,
            ActionPair(C.REVIEWS_FILTERS_EDIT_OWN, C.REVIEWS_FILTERS_EDIT_OTHER),
            apply_review_defaults,
            request,
        )
    try:
        return apply_new_defaults(
            db, access, request, http_request.state.access_credential.session_id
        )
    except FilterPreferenceConflict as exc:
        raise root._review_http_error(exc.code, exc.detail) from exc
