"""MU-35 HTTP harness: personal exports, the account-link routes, and a synthetic Jellyfin.

Accounts are `mu04-access-builders-3` (owner 1, admins 2 and 3, users 4 and 5;
reauthentication passwords are the stand-in Jellyfin passwords). Reviews and media
come from the MU-31 personal-export fixtures. Jellyfin is MU-08's `JellyfinBoundary`
(`editarr-jellyfin-users-1`) on loopback, a labelled substitute for a live server. One
fixed clock drives sessions, link verification, and proof expiry.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

import scanner.http.account_link_api as account_link_api
import scanner.http.jellyfin_accounts_api as jellyfin_accounts_api
from scanner.db import (
    AccountLinkTransitionRow,
    AuditEventRow,
    JellyfinSettingsRow,
    MediaItemExportRow,
    UserProviderIdentityRow,
)
from scanner.http.auth_rate_limit import LoginRateLimiter
from scanner.security import auth_policy
from scanner.security.access import link_state
from scanner.security.account_links.proofs import ProofRegistry
from scanner.security.jellyfin_accounts.sync import SyncRegistry
from tests.jellyfin_user_boundary import IDS, JellyfinBoundary
from tests.multi_user_access_support import MEMBER_PASSWORD, OWNER, OWNER_PASSWORD
from tests.test_personal_exports_http import publish, seeded_review

REPLACED_CASEY = "e5000000000000000000000000000002"
PASSWORDS = {user_id: f"jf-{name.lower()}-password" for name, user_id in IDS.items()}
PASSWORDS[REPLACED_CASEY] = "jf-casey-replacement-password"


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fixed = Clock()
    monkeypatch.setattr(link_state, "link_clock", fixed)
    monkeypatch.setattr(auth_policy, "session_now", fixed)
    return fixed


@pytest.fixture
def boundary():
    with JellyfinBoundary(secrets.token_urlsafe(24), PASSWORDS) as server:
        yield server


@pytest.fixture
def link_app(app, boundary, clock, monkeypatch):
    """The personal-export app with the link routes and Jellyfin configured."""
    app.client.app.include_router(account_link_api.router)
    app.client.app.include_router(jellyfin_accounts_api.router)
    monkeypatch.setattr(jellyfin_accounts_api, "registry", SyncRegistry())
    monkeypatch.setattr(account_link_api, "link_proof_limiter", LoginRateLimiter())
    monkeypatch.setattr(account_link_api, "proofs", ProofRegistry())
    with app.accounts.factory() as db:
        db.merge(JellyfinSettingsRow(id=1, server_url=boundary.url, api_key=boundary.api_key))
        db.commit()
    app.boundary = boundary
    app.clock = clock
    return app


def password_of(user_id: int) -> str:
    return OWNER_PASSWORD if user_id == OWNER else MEMBER_PASSWORD


def jellyfin(name: str, user_id: str | None = None) -> dict:
    return {"username": name, "password": PASSWORDS[user_id or IDS[name]]}


def request_proof(app, actor, target, name, *, user_id=None):
    body = {
        "reauthentication": {"password": password_of(actor)},
        "jellyfin": jellyfin(name, user_id),
    }
    return app.send(
        "POST", f"/api/users/{target}/link/proof", app.accounts.unsafe_cookie(actor), body
    )


def confirm(app, actor, target, proof, confirmation, *, headers=None):
    body = {
        "reauthentication": {"password": password_of(actor)},
        "proof": proof,
        "confirmation": confirmation,
    }
    return app.send(
        "POST",
        f"/api/users/{target}/link",
        headers or app.accounts.unsafe_cookie(actor),
        body,
    )


def prove_and_link(app, actor, target, name, **kwargs):
    proven = request_proof(app, actor, target, name, **kwargs)
    assert proven.status_code == 200, proven.text
    body = proven.json()
    return confirm(app, actor, target, body["proof"], body["confirmation"])


def publications(app, user_id) -> list[tuple]:
    with app.accounts.factory() as db:
        return [
            (
                row.id,
                row.format.value,
                row.target_export_id,
                Path(row.destination_path),
                row.content_bytes,
                row.publication_revision,
            )
            for row in db.query(MediaItemExportRow)
            .filter_by(target_user_id=user_id)
            .order_by(MediaItemExportRow.id)
        ]


def publish_both(app, owner, name):
    review = seeded_review(app, owner, name)
    for export_format in ("edl", "mcf"):
        response = publish(app, review, export_format, user=owner)
        assert response.status_code == 200, response.text
    return review


def identity(app, user_id):
    with app.accounts.factory() as db:
        row = db.get(UserProviderIdentityRow, user_id)
        return None if row is None else (row.server_id, row.provider_user_id, row.link_state)


def transitions(app, user_id):
    with app.accounts.factory() as db:
        return [
            (row.state, row.failure_reason, [move.outcome for move in row.moves])
            for row in db.scalars(
                select(AccountLinkTransitionRow)
                .filter_by(user_id=user_id)
                .order_by(AccountLinkTransitionRow.id)
            )
        ]


def link_events(app) -> list[tuple]:
    with app.accounts.factory() as db:
        return [
            (row.outcome, row.actor_id, row.target_user_id, json.loads(row.details_json))
            for row in db.scalars(
                select(AuditEventRow).filter_by(action="account.link").order_by(AuditEventRow.id)
            )
        ]


def audit_text(app) -> str:
    with app.accounts.factory() as db:
        return "\n".join(row.details_json for row in db.scalars(select(AuditEventRow)))


def import_jellyfin_user(app, name: str) -> int:
    """Import a Jellyfin user as its own account through MU-16's admin route."""
    response = app.send(
        "POST",
        "/api/jellyfin/users/import",
        app.accounts.unsafe_cookie(OWNER),
        {"user_ids": [IDS[name]]},
    )
    assert response.status_code == 200, response.text
    return response.json()["results"][0]["user_id"]


def sync(app) -> None:
    response = app.send("POST", "/api/jellyfin/sync", app.accounts.unsafe_cookie(OWNER), {})
    assert response.status_code == 200, response.text
