"""Shared setup for the JF-04 Quick Connect HTTP tests.

Jellyfin is the local `JellyfinBoundary`, which answers Quick Connect the way the
pinned Jellyfin 12 did in the JF-04 probe: Initiate needs device fields, Connect
answers 404 for an unknown secret, and sign-in does not spend the secret.
"""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy import func, select

import scanner.http.jellyfin_quick_connect_api as quick_connect_api
from scanner.db import AuthSessionRow
from scanner.security import auth_policy
from scanner.security.access import link_state
from scanner.security.jellyfin_accounts.quick_connect import QuickConnectAttempts, RecentAnswer
from tests.jellyfin_user_boundary import JellyfinBoundary
from tests.multi_user_access_support import ORIGIN
from tests.test_jellyfin_accounts_http import PASSWORDS, Clock, _app

QC = "/api/auth/jellyfin/quick-connect"
HANDLE = "editarr_session_quick_connect"


class Ticks:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def boundary():
    with JellyfinBoundary(secrets.token_urlsafe(24), PASSWORDS) as server:
        yield server


@pytest.fixture
def app(monkeypatch, tmp_path, boundary):
    fixed = Clock()
    monkeypatch.setattr(link_state, "link_clock", fixed)
    monkeypatch.setattr(auth_policy, "session_now", fixed)
    ticks = Ticks()
    monkeypatch.setattr(
        quick_connect_api, "quick_connect_attempts", QuickConnectAttempts(clock=ticks)
    )
    monkeypatch.setattr(quick_connect_api, "quick_connect_offered", RecentAnswer(10, clock=ticks))
    users = _app(monkeypatch, tmp_path, boundary)
    users.client.app.include_router(quick_connect_api.router)
    users.ticks = ticks
    users.client.base_url = "https://editarr.example"
    yield users
    users.client.close()
    users.accounts.close()


def _start(app):
    response = app.client.post(f"{QC}/initiate", headers=ORIGIN)
    assert response.status_code == 201, response.text
    return response.json()["code"]


def _status(app):
    """Poll as the browser does, a second after the last poll."""
    app.ticks.now += 1
    return app.client.get(f"{QC}/status", headers=ORIGIN)


def _complete(app, **body):
    return app.client.post(f"{QC}/complete", json=body, headers=ORIGIN)


def _sessions(app) -> int:
    return app.scalar(select(func.count()).select_from(AuthSessionRow))


def _use_handle(app, handle):
    app.client.cookies.clear()
    app.client.cookies.set(HANDLE, handle, domain="editarr.example")
