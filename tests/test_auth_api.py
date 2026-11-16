"""Endpoint-level checks for the bounded AUTH-08 router contract."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import scanner.http.auth_api as auth_api
from scanner.db import AuthSettingsRow, Base, get_db
from scanner.http.auth_api import router
from scanner.settings.config import Settings
from tests.jellyfin_setup_fake import install, setup_body
from tests.multi_user_access_support import add_linked_owner


def test_setup_status_and_cookie_contract_keep_a_stable_csrf_token(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    application = FastAPI()
    application.include_router(router)

    def test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = test_db
    monkeypatch.setenv("EDITARR_AUTH_COOKIE_SECURE", "false")
    monkeypatch.setattr(auth_api, "settings", Settings(data_dir=tmp_path))
    install(monkeypatch)
    try:
        with TestClient(application) as client:
            initial = client.get("/api/auth/status")
            assert initial.json()["state"] == "setup_required"

            setup = client.post(
                "/api/auth/setup",
                json=setup_body(remember_me=True, return_to="/queue?tab=active"),
            )
            assert setup.status_code == 201
            csrf_token = setup.json()["csrf_token"]
            assert setup.headers["cache-control"] == "no-store"
            assert setup.headers["pragma"] == "no-cache"
            cookie = setup.headers["set-cookie"]
            assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
            assert "Max-Age=2592000" in cookie and "Secure" not in cookie

            first_status = client.get("/api/auth/status")
            second_status = client.get("/api/auth/status")
            assert first_status.json()["csrf_token"] == csrf_token
            assert second_status.json()["csrf_token"] == csrf_token
            assert first_status.headers["cache-control"] == "no-store"

            logout = client.post("/api/auth/logout")
            assert logout.status_code == 200
            assert logout.json()["state"] == "unauthenticated"
            assert "Max-Age=0" in logout.headers["set-cookie"]
    finally:
        engine.dispose()


def test_setup_rejects_a_request_without_a_server_before_entering_endpoint(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    application = FastAPI()
    application.include_router(router)

    def test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = test_db
    monkeypatch.setattr(auth_api, "settings", Settings(data_dir=tmp_path))
    try:
        with TestClient(application) as client:
            response = client.post(
                "/api/auth/setup",
                json={"username": "Admin", "password": "any password"},
            )

            assert response.status_code == 422
            assert response.json()["detail"][0]["loc"] == ["body", "server_url"]
            assert client.get("/api/auth/status").json()["state"] == "setup_required"
    finally:
        engine.dispose()


def test_status_ignores_persisted_legacy_bypass_modes_and_names_no_account(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    application = FastAPI()
    application.include_router(router)

    def test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = test_db
    monkeypatch.setattr(auth_api, "settings", Settings(data_dir=tmp_path))
    try:
        with TestClient(application, client=("127.0.0.1", 50000)) as client:
            with factory() as db:
                add_linked_owner(db, "Admin")
            with factory() as db:
                db.add(
                    AuthSettingsRow(
                        id=1,
                        method="external",
                        authentication_required="disabled_for_local_addresses",
                    )
                )
                db.commit()

            response = client.get("/api/auth/status")

            assert response.status_code == 200
            assert response.json()["state"] == "unauthenticated"
            assert response.json()["csrf_token"] is None
            assert "configuration" not in response.json()
            assert response.json()["sign_in"] == {"jellyfin": False}
    finally:
        engine.dispose()


def test_setup_refuses_an_external_return_target(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    application = FastAPI()
    application.include_router(router)

    def test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = test_db
    monkeypatch.setattr(auth_api, "settings", Settings(data_dir=tmp_path))
    install(monkeypatch)
    try:
        with TestClient(application) as client:
            invalid_return = client.post(
                "/api/auth/setup", json=setup_body(return_to="https://example.invalid/")
            )
            assert invalid_return.status_code == 422
            assert client.get("/api/auth/status").json()["state"] == "setup_required"
    finally:
        engine.dispose()
