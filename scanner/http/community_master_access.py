"""Shared access, client binding and error rendering for the master-set routes.

Every master link, its filters, votes and notifications belong to the calling
account; another account's link is `404`. Media access applies to the link's local
version: the account must reach one of its catalog locations with the route's
capability (local API §1).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, Response

from scanner.db import CommunityMasterLinkRow, MediaItemRow
from scanner.integrations.community.activity_access import ActivityAccess
from scanner.integrations.community.transport import CommunityFailure
from scanner.modeling.community_master_alignment import AlignmentFitError
from scanner.persistence.repository.community_master_links import (
    CommunityMasterError,
    get_link,
)
from scanner.persistence.repository.review_access import ReviewAccess, not_found
from scanner.persistence.repository.scan_jobs import ScanMedia
from scanner.persistence.rows.db_media_identity import MediaVersionLocatorRow
from scanner.runtime.scan_version import ScanVersionChanged
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.records import MediaAllowed
from scanner.security.access.targets import AccountRef, MediaRef
from scanner.security.private_identity_files import IdentityUnavailable

NO_STORE = {"Cache-Control": "no-store"}
# Remote or local codes that mean the caller's object does not exist for them.
_HIDDEN = {"not_found", "filter_not_found", "vote_intent_unavailable"}
# Contract error details the web reads from the error body.
_DETAILS = ("reason", "mergedInto")


def json_response(document: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(document, status_code=status_code, headers=NO_STORE)


def no_content() -> Response:
    return Response(status_code=204, headers=NO_STORE)


def conflict(code: str, *, retryable: bool = False, **details) -> JSONResponse:
    status = 503 if retryable or code == "not_configured" else 409
    return json_response({"code": code, "retryable": retryable, **details}, status)


def failure(error: Exception) -> Response:
    """Render a refused master-set operation, or re-raise what is not one."""
    if isinstance(error, CommunityMasterError):
        if error.code in _HIDDEN:
            raise not_found() from error
        return conflict(error.code)
    if isinstance(error, CommunityFailure):
        if error.code in _HIDDEN:
            raise not_found() from error
        if error.code == "rate_limited":
            wait = int(error.details["retryAfter"])
            response = json_response(
                {"code": error.code, "retryable": True, "retryAfter": wait}, 429
            )
            response.headers["Retry-After"] = str(wait)
            return response
        details = {key: error.details[key] for key in _DETAILS if key in error.details}
        return conflict(error.code, retryable=error.retryable, **details)
    if isinstance(error, AlignmentFitError):
        return conflict(error.code)
    if isinstance(error, ScanVersionChanged):
        return conflict("target_changed")
    if isinstance(error, IdentityUnavailable):
        return conflict("identity_" + error.status)
    raise error


_HANDLED = (
    CommunityMasterError,
    CommunityFailure,
    AlignmentFitError,
    ScanVersionChanged,
    IdentityUnavailable,
)


def respond(operation: Callable[[], Any], status_code: int = 200) -> Response:
    """Run a synchronous operation and render its JSON, `204` for None, or its failure."""
    try:
        result = operation()
    except _HANDLED as error:
        return failure(error)
    return no_content() if result is None else json_response(result, status_code)


async def respond_async(operation: Callable[[], Any]) -> Response:
    """`respond` for an operation that awaits the community service."""
    try:
        result = await operation()
    except _HANDLED as error:
        return failure(error)
    return no_content() if result is None else json_response(result)


def invalid(field: str, message: str) -> RequestValidationError:
    """A 422 in the existing validation-list shape for a semantic body error."""
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", field), "msg": message, "input": None}]
    )


def require_confirmed(confirmed: bool) -> None:
    if confirmed is not True:
        raise CommunityFailure("confirmation_required")


def require_capability(access: PrincipalAccess, capability: C) -> None:
    """`403 capability_denied` before any object is looked up."""
    if capability not in access.capabilities:
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)


def version_locations(db: Session, media_version_id: str):
    """`(locator, media item)` pairs naming the catalog locations of a local version."""
    return db.execute(
        select(MediaVersionLocatorRow, MediaItemRow)
        .join(MediaItemRow, MediaItemRow.id == MediaVersionLocatorRow.media_item_id)
        .where(MediaVersionLocatorRow.version_id == media_version_id)
        .order_by(MediaVersionLocatorRow.media_item_id, MediaVersionLocatorRow.media_source_id)
    ).all()


def location_media(locator, item) -> ScanMedia:
    return ScanMedia(item.server_id, item.jellyfin_id, item.library_id, locator.resolved_path)


def media_allowed(access: PrincipalAccess, decision, capability: C) -> bool:
    """Whether `capability` may act on media the account may reach."""
    if not isinstance(decision, MediaAllowed):
        return False
    if capability == C.COMMUNITY_FEEDBACK_SUBMIT:
        principal = access.principal
        target = AccountRef(principal.user_id, principal.account.role)
        return isinstance(decide(access, capability, target), Allowed)
    return isinstance(decide(access, capability, MediaRef(decision)), Allowed)


def authorized_location(db: Session, access: PrincipalAccess, media_version_id: str, capability):
    """The first location of a local version the account may use, or `404`."""
    reviews = ReviewAccess(db, access)
    for locator, item in version_locations(db, media_version_id):
        media = location_media(locator, item)
        if media_allowed(access, reviews.media_decision(media), capability):
            return locator, item
    raise not_found()


def authorize_link(
    db: Session, access: PrincipalAccess, link_id: str, capability: C
) -> CommunityMasterLinkRow:
    """The account's own link whose local version it may use with `capability`."""
    require_capability(access, capability)
    link = get_link(db, access.principal.user_id, link_id)
    if link is None:
        raise not_found()
    authorized_location(db, access, link.media_version_id, capability)
    return link


def bound_client(db: Session, access: PrincipalAccess, media: ScanMedia, capability: C):
    """The caller's community client, rechecking media and capability before each send."""
    from .community_identity_api import local_client

    client = local_client(db, access)
    return ActivityAccess(ReviewAccess(db, access), media, capability).bind(client)


def link_client(db: Session, access: PrincipalAccess, link_id: str, capability: C):
    """`authorize_link`, then a client bound to the link's authorized location."""
    link = authorize_link(db, access, link_id, capability)
    locator, item = authorized_location(db, access, link.media_version_id, capability)
    media = location_media(locator, item)
    db.rollback()
    return bound_client(db, access, media, capability)
