"""The authentication status every sign-in route answers with.

A signed-in response names the session's own account and carries MU-04's
`CapabilityPayload`, so the web client renders permissions from the same
response that admits it and refetches when `permission_revision` changes. A
signed-out response carries only the sign-in options anyone may see.
"""

from __future__ import annotations

from typing import Literal

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.persistence.repository.repository_settings import get_jellyfin_credentials
from scanner.persistence.rows.db_auth import UserProfileRow, UserRow
from scanner.security.access.authorization import capability_payload
from scanner.security.access.identity import CredentialKind, UserId
from scanner.security.access.load import load_access

from .auth_api_models import AuthenticationStatusResponse, SignInOptions


def sign_in_options(db: Session) -> SignInOptions:
    """Which sign-in entry points this installation offers to a visitor."""

    return SignInOptions(jellyfin=get_jellyfin_credentials(db) is not None)


def signed_out(
    db: Session,
    request: Request,
    state: Literal["setup_required", "unauthenticated"],
    *,
    return_to: str | None = None,
) -> AuthenticationStatusResponse:
    return AuthenticationStatusResponse(
        state=state,
        setup_required=state == "setup_required",
        sign_in=sign_in_options(db),
        return_to=return_to,
    )


def signed_in(
    db: Session,
    request: Request,
    *,
    user_id: int,
    session_id: int,
    csrf_token: str,
    return_to: str | None = None,
) -> AuthenticationStatusResponse:
    """The session's own account and live capabilities, or signed out if it is gone."""

    access = load_access(db, UserId(user_id), CredentialKind.SESSION, session_id)
    if access is None:
        return signed_out(db, request, "unauthenticated", return_to=return_to)
    names = db.execute(
        select(UserRow.username, UserProfileRow.display_name)
        .outerjoin(UserProfileRow, UserProfileRow.user_id == UserRow.id)
        .where(UserRow.id == user_id)
    ).one()
    provider = access.principal.account.provider
    return AuthenticationStatusResponse(
        state="authenticated",
        setup_required=False,
        display_name=names.display_name or names.username,
        jellyfin_user_id=provider.user_id.value if provider else None,
        access=capability_payload(access),
        csrf_token=csrf_token,
        sign_in=sign_in_options(db),
        return_to=return_to,
    )
