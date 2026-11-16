"""API key header credentials, wrapped storage, and browser boundaries.

Issue, reveal, and per-user cases through real passwords live in
`test_api_key_http.py`; this harness has no verifiable password.
"""

import hashlib
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import scanner.http.auth_api as auth_api
from scanner.db import ApiKeyRow, AuthSettingsRow, Base, get_db
from scanner.http.auth_api import router
from scanner.security import auth_policy
from scanner.security.api_key_crypto import load_api_key_wrap_key
from scanner.security.api_keys import authenticate_api_key, rotate_api_key
from scanner.security.auth_repository import owner_id
from tests.test_auth_policy import _deployment_snapshot, _session_headers, policy_app

# Reuse the same adversarial middleware harness and administrator session.
__all__ = ["policy_app"]


@pytest.fixture
def key_app(policy_app):
    client, calls, issued = policy_app
    client.app.include_router(router)
    factory = auth_policy.get_session_factory()

    def database():
        with factory() as db:
            yield db

    client.app.dependency_overrides[get_db] = database
    with factory() as db:
        key = rotate_api_key(db, owner_id(db), load_api_key_wrap_key(auth_api.settings.data_dir))
    return client, calls, issued, key, factory


def test_key_is_stored_wrapped_and_shared_across_sessions(key_app):
    client, _, _, key, factory = key_app
    with factory() as db:
        row = db.get(ApiKeyRow, 1)
        assert row.token_digest == hashlib.sha256(key.encode()).hexdigest()
        assert row.token_value is None
        assert row.secret_envelope.startswith("v1.") and key not in row.secret_envelope
        assert authenticate_api_key(db, key).user_id == row.user_id == owner_id(db)
    headers = {"X-Api-Key": key}
    assert client.get("/api/protected", headers=headers).status_code == 200
    assert client.post("/api/mutate", headers=headers).status_code == 200
    assert client.put("/api/backups/schedule", headers=headers).status_code == 200
    status = client.get("/api/auth/api-key", headers=headers)
    assert status.json() == {
        "configured": True,
        "created_at": status.json()["created_at"],
        "revealable": True,
    }
    assert status.headers["cache-control"] == "no-store"
    assert key not in status.text


def test_a_key_cannot_issue_or_reveal_but_can_revoke_itself(key_app):
    client, _, _, key, _ = key_app
    headers = {"X-Api-Key": key}
    for path in ("/api/auth/api-key", "/api/auth/api-key/reveal"):
        refused = client.post(path, headers=headers, json={})
        assert (refused.status_code, refused.json()["code"]) == (
            403,
            "interactive_session_required",
        )
    assert client.get("/api/protected", headers=headers).status_code == 200
    assert client.delete("/api/auth/api-key", headers=headers).status_code == 200
    assert client.get("/api/protected", headers=headers).status_code == 401


def test_invalid_repeated_and_query_keys_never_fall_back_to_session(key_app):
    client, calls, issued, key, _ = key_app
    for candidate in ["bad", key.upper(), "a" * 64, key + "," + key]:
        response = client.post(
            "/api/mutate",
            headers={
                **_session_headers(issued),
                "X-CSRF-Token": issued.csrf_token,
                "X-Api-Key": candidate,
            },
        )
        assert response.status_code == 401
    assert (
        client.get("/api/protected", headers=[("X-Api-Key", key), ("X-Api-Key", key)]).status_code
        == 401
    )
    assert client.get("/api/protected", params={"apikey": key}).status_code == 401
    assert calls == {"get": 0, "mutate": 0}


def test_keys_cannot_bypass_origin_checks_or_browser_csrf(key_app):
    client, calls, issued, key, _ = key_app
    assert (
        client.post(
            "/api/mutate", headers={"X-Api-Key": key, "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/mutate", headers={"X-Api-Key": key, "Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    assert client.post("/api/auth/api-key", headers=_session_headers(issued)).status_code == 403
    response = client.post(
        "/api/auth/api-key",
        headers={
            **_session_headers(issued),
            "X-CSRF-Token": issued.csrf_token,
        },
        json={},
    )
    assert (response.status_code, response.json()["code"]) == (403, "reauthentication_required")
    assert client.get("/api/protected", headers=_session_headers(issued)).status_code == 200
    assert calls["mutate"] == 0


def test_legacy_bypass_rows_admit_nothing_and_invalid_keys_fail_closed(key_app):
    client, _, _, key, factory = key_app
    with factory() as db:
        db.merge(
            AuthSettingsRow(
                id=1, method="external", authentication_required="disabled_for_local_addresses"
            )
        )
        db.commit()
    assert client.get("/api/protected").status_code == 401
    assert client.get("/api/auth/api-key").status_code == 401
    assert client.post("/api/auth/api-key").status_code == 401
    assert client.get("/api/protected", headers={"X-Api-Key": "bad"}).status_code == 401
    assert client.get("/api/auth/api-key", headers={"X-Api-Key": key}).status_code == 200


def test_key_does_not_open_unconfigured_installation(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    key = "c" * 64
    with factory() as db:
        db.add(
            ApiKeyRow(
                token_digest=hashlib.sha256(key.encode()).hexdigest(),
                token_value=key,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    monkeypatch.setattr(auth_policy, "get_session_factory", lambda: factory)
    app = FastAPI()
    app.state.deployment_snapshot = _deployment_snapshot("https://editarr.example")
    app.add_middleware(auth_policy.AuthPolicyMiddleware)
    with TestClient(app) as client:
        assert client.get("/openapi.json", headers={"X-Api-Key": key}).status_code == 423
    engine.dispose()


def test_key_authenticates_community_and_docs_routes(key_app):
    client, _, _, key, _ = key_app

    @client.app.get("/api/community/example")
    def community():
        return {"ok": True}

    headers = {"X-Api-Key": key}
    assert client.get("/api/community/example", headers=headers).status_code == 200
    assert client.get("/openapi.json", headers=headers).status_code == 200


def test_key_respects_configured_application_prefix(key_app):
    client, _, _, key, _ = key_app
    client.app.root_path = "/editarr"
    client.app.state.deployment_snapshot = _deployment_snapshot("https://editarr.example/editarr")
    headers = {"X-Api-Key": key}
    assert client.get("/editarr/api/protected", headers=headers).status_code == 200
    assert client.get("/api/protected", headers=headers).status_code == 404
    assert client.get("/editarr/api/auth/api-key", headers=headers).status_code == 200


def test_a_digest_only_key_still_authenticates_but_cannot_be_revealed(key_app):
    client, _, issued, key, factory = key_app
    headers = _session_headers(issued)
    assert client.get("/api/auth/api-key", headers=headers).json()["revealable"] is True
    with factory() as db:
        row = db.get(ApiKeyRow, 1)
        row.secret_envelope = None
        db.commit()
    legacy = client.get("/api/auth/api-key", headers=headers)
    assert legacy.json()["configured"] is True
    assert legacy.json()["revealable"] is False
    assert client.get("/api/protected", headers={"X-Api-Key": key}).status_code == 200
