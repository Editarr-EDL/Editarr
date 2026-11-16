"""TEST FIXTURE HTTP boundary for MU-04 builders (`mu04-access-builders-3`).

`access_fixture_app` serves the real auth router and access middleware in
front of fixture routes that resolve explicit targets and call the shared
authorization interface. `/api/protected` decides nothing, so any admitted
account reaches it. These are not product endpoints.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

import scanner.http.auth_api as auth_api
import scanner.security.auth_policy as auth_policy
from scanner.db import UserRow, get_db
from scanner.security.access.api_shapes import AccessErrorCode, Reauthentication
from scanner.security.access.authorization import capability_payload, require
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.fresh import verify_reauthentication
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import UserId
from scanner.security.access.request_access import install_access_errors, request_access
from scanner.security.access.targets import INSTANCE, NO_MEDIA, AccountRef, ActionPair, ResourceRef
from scanner.security.access.vocabulary import Role
from tests.multi_user_access_support import ALLOWED_MEDIA, DENIED_MEDIA, LiveAccounts
from tests.test_auth_policy import _deployment_snapshot

_MEDIA = {"allowed": ALLOWED_MEDIA, "denied": DENIED_MEDIA, "none": NO_MEDIA}


class _FreshRequest(BaseModel):
    password: str | None = None


def _account_target(db: Session, user_id: int) -> AccountRef:
    role = db.scalar(select(UserRow.role).where(UserRow.id == user_id))
    if role is None:
        raise AccessDenied(AccessErrorCode.NOT_FOUND)
    return AccountRef(UserId(user_id), Role(role))


def access_fixture_app(accounts: LiveAccounts) -> FastAPI:
    """Real auth router and middleware with fixture routes that resolve explicit targets."""

    application = FastAPI()
    application.state.deployment_snapshot = _deployment_snapshot("https://editarr.example")
    application.include_router(auth_api.router)
    install_access_errors(application)

    def database():
        with accounts.factory() as session:
            yield session

    application.dependency_overrides[get_db] = database
    principal = Depends(request_access)

    @application.get("/api/protected")
    def protected():
        return {"ok": True}

    @application.get("/api/test-access/payload")
    def payload(access: PrincipalAccess = principal):
        return capability_payload(access).model_dump(mode="json")

    @application.get("/api/test-access/instance/{capability}")
    def instance(capability: Capability, access: PrincipalAccess = principal):
        return {"capability": require(access, capability, INSTANCE)}

    @application.get("/api/test-access/accounts/{user_id}")
    def account(
        user_id: int,
        own: Capability,
        other: Capability,
        access: PrincipalAccess = principal,
        db: Session = Depends(get_db),
    ):
        target = _account_target(db, user_id)
        return {"capability": require(access, ActionPair(own, other), target)}

    @application.get("/api/test-access/resources/{owner_id}")
    def resource(
        owner_id: int,
        own: Capability,
        other: Capability,
        media: str = "none",
        visible: bool = False,
        access: PrincipalAccess = principal,
        db: Session = Depends(get_db),
    ):
        target = ResourceRef(_account_target(db, owner_id), _MEDIA[media], visible)
        return {"capability": require(access, ActionPair(own, other), target)}

    @application.post("/api/test-access/fresh/{capability}")
    def fresh(
        capability: Capability,
        body: _FreshRequest,
        account: int | None = None,
        access: PrincipalAccess = principal,
        db: Session = Depends(get_db),
    ):
        target = INSTANCE if account is None else _account_target(db, account)
        proof = (
            verify_reauthentication(
                db, access, Reauthentication(password=body.password), accounts.verify
            )
            if body.password is not None
            else None
        )
        return {"capability": require(access, capability, target, fresh=proof)}

    application.add_middleware(
        auth_policy.AuthPolicyMiddleware, configuration=accounts.configuration
    )
    return application


def serve_access_fixture(monkeypatch, accounts: LiveAccounts):
    """A TestClient for `access_fixture_app` with stand-in Jellyfin passwords; the caller
    closes it."""

    monkeypatch.setattr(auth_policy, "get_session_factory", lambda: accounts.factory)
    monkeypatch.setattr(auth_api, "settings", accounts.configuration)
    accounts.stand_in_jellyfin(monkeypatch)
    return TestClient(access_fixture_app(accounts))
