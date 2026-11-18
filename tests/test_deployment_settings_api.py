"""Deployment-settings API coverage for staged reverse-proxy configuration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import scanner.http.deployment_api as deployment_api
import scanner.security.auth_policy as auth_policy
from scanner.api import PrefixRoutingMiddleware
from scanner.db import AuthSettingsRow, Base, get_db
from scanner.http.deployment_api import router
from scanner.security.auth_repository import issue_session
from scanner.settings.config import Settings
from scanner.settings.deployment_config import (
    DeploymentConfigurationUnavailable,
    DeploymentSnapshot,
    DeploymentStore,
    parse_base_url,
    resolve_deployment_snapshot,
)
from tests.multi_user_access_support import add_linked_owner


def _snapshot(
    base_url: str = "https://editarr.example/editarr",
    *,
    port: int = 3348,
    cli_port_override: int | None = None,
) -> DeploymentSnapshot:
    parsed = parse_base_url(base_url)
    return DeploymentSnapshot(
        base_url=parsed.base_url,
        public_origin=parsed.public_origin,
        prefix=parsed.prefix,
        listening_port=port,
        base_url_source="saved",
        port_source="cli" if cli_port_override is not None else "saved",
        cli_port_override=cli_port_override,
        cookie_secure=True,
        session_not_before=None,
        legacy_cookie_paths=(),
    )


def _application(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    snapshot: DeploymentSnapshot = _snapshot(),
    host: str = "127.0.0.1",
):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    configuration = Settings(data_dir=tmp_path)
    monkeypatch.setattr(auth_policy, "get_session_factory", lambda: factory)
    monkeypatch.setattr(deployment_api, "settings", configuration)

    db = factory()
    try:
        add_linked_owner(db, "Admin")
        session = issue_session(db, remember_me=False, configuration=configuration)
    finally:
        db.close()

    application = FastAPI()
    application.state.deployment_snapshot = snapshot
    application.state.launch_context = SimpleNamespace(
        host=host, cli_port_override=snapshot.cli_port_override
    )
    application.root_path = snapshot.prefix
    application.include_router(router)

    def database():
        with factory() as session:
            yield session

    application.dependency_overrides[get_db] = database
    application.add_middleware(auth_policy.AuthPolicyMiddleware, configuration=configuration)
    application.add_middleware(PrefixRoutingMiddleware)
    return application, factory, configuration, session, engine


def _headers(configuration: Settings, session, origin: str) -> dict[str, str]:
    return {
        "Cookie": f"{configuration.auth_cookie_name}={session.token}",
        "Origin": origin,
        "X-CSRF-Token": session.csrf_token,
    }


def test_read_exposes_immutable_active_and_seeded_next_start_at_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITARR_BASE_URL", "https://editarr.example/editarr/")
    application, _, configuration, session, engine = _application(monkeypatch, tmp_path)
    try:
        with TestClient(application) as client:
            response = client.get(
                "/editarr/api/settings/deployment",
                headers=_headers(configuration, session, "https://editarr.example"),
            )
        assert response.status_code == 200
        assert response.json() == {
            "active": {
                "base_url": "https://editarr.example/editarr",
                "public_origin": "https://editarr.example",
                "prefix": "/editarr",
                "listening_port": 3348,
                "base_url_source": "saved",
                "port_source": "saved",
                "cli_port_override": None,
            },
            "next_start": {
                "base_url": "https://editarr.example/editarr",
                "public_origin": "https://editarr.example",
                "prefix": "/editarr",
                "listening_port": 3348,
                "base_url_source": "environment",
                "port_source": "default",
                "cli_port_override": None,
            },
            "saved": {"base_url": None, "port": None},
            "restart_required": False,
            "macos_dock_supported": False,
            "restart": {
                "supported": False,
                "mode": "unsupported",
                "generation": 0,
                "in_progress": False,
                "outcome": "idle",
                "target_generation": None,
                "reason": "manual_uvicorn",
                "detail": "Restart Editarr requires the standalone editarr launcher",
            },
        }
    finally:
        engine.dispose()


def test_explicit_root_save_overrides_environment_and_only_stages_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITARR_BASE_URL", "https://editarr.example/editarr")
    application, _, configuration, session, engine = _application(monkeypatch, tmp_path)
    try:
        with TestClient(application) as client:
            response = client.put(
                "/editarr/api/settings/deployment",
                headers=_headers(configuration, session, "https://editarr.example"),
                json={"base_url": "https://EDITARR.example:443/"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["active"]["prefix"] == "/editarr"
            assert payload["next_start"]["base_url"] == "https://editarr.example"
            assert payload["next_start"]["prefix"] == "/"
            assert payload["saved"] == {"base_url": "https://editarr.example", "port": None}
            assert payload["restart_required"] is True

            persisted = DeploymentStore(tmp_path).read()
            assert persisted.base_url == "https://editarr.example"
            assert persisted.cookie_scope.pending_previous_path == "/editarr"

            # A new resolver reads the explicit root rather than the environment prefix.
            reread = deployment_api.resolve_deployment_snapshot(
                store=DeploymentStore(tmp_path), auth_cookie_secure=True
            )
            assert reread.base_url == "https://editarr.example"
            assert reread.prefix == "/"
            assert application.state.deployment_snapshot.prefix == "/editarr"
    finally:
        engine.dispose()


def test_port_only_save_preserves_base_url_seed_and_pending_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITARR_BASE_URL", "https://editarr.example/editarr")
    application, _, configuration, session, engine = _application(monkeypatch, tmp_path)
    try:
        with TestClient(application) as client:
            response = client.put(
                "/editarr/api/settings/deployment",
                headers=_headers(configuration, session, "https://editarr.example"),
                json={"port": 9001},
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["saved"] == {"base_url": None, "port": 9001}
        assert payload["next_start"]["base_url_source"] == "environment"
        assert payload["next_start"]["listening_port"] == 9001
        assert payload["restart_required"] is True
        assert DeploymentStore(tmp_path).read().cookie_scope.pending_previous_path is None
    finally:
        engine.dispose()


def test_local_default_port_save_derives_next_url_without_staging_reauthentication(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("EDITARR_BASE_URL", raising=False)
    local_snapshot = resolve_deployment_snapshot(
        store=DeploymentStore(tmp_path), auth_cookie_secure=True
    )
    application, _, configuration, session, engine = _application(
        monkeypatch, tmp_path, snapshot=local_snapshot
    )
    try:
        with TestClient(application, base_url="http://localhost:3348") as client:
            response = client.put(
                "/api/settings/deployment",
                headers=_headers(configuration, session, "http://localhost:3348"),
                json={"port": 9001},
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["active"]["base_url"] == "http://localhost:3348"
        assert payload["next_start"]["base_url"] == "http://localhost:9001"
        assert payload["next_start"]["base_url_source"] == "local_default"
        assert payload["saved"] == {"base_url": None, "port": 9001}
        assert payload["restart_required"] is True
        assert DeploymentStore(tmp_path).read().cookie_scope.pending_previous_path is None
    finally:
        engine.dispose()


def test_cli_port_override_masks_saved_port_for_restart_requirement(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITARR_BASE_URL", "https://editarr.example/editarr")
    snapshot = _snapshot(port=8010, cli_port_override=8010)
    application, _, configuration, session, engine = _application(
        monkeypatch, tmp_path, snapshot=snapshot
    )
    try:
        with TestClient(application) as client:
            response = client.put(
                "/editarr/api/settings/deployment",
                headers=_headers(configuration, session, "https://editarr.example"),
                json={"port": 9001},
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["saved"] == {"base_url": None, "port": 9001}
        assert payload["next_start"]["listening_port"] == 8010
        assert payload["next_start"]["port_source"] == "cli"
        assert payload["next_start"]["cli_port_override"] == 8010
        assert payload["restart_required"] is False
    finally:
        engine.dispose()


def test_invalid_or_failed_write_preserves_the_previous_file(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITARR_BASE_URL", "https://editarr.example/editarr")
    application, _, configuration, session, engine = _application(monkeypatch, tmp_path)
    headers = _headers(configuration, session, "https://editarr.example")
    try:
        with TestClient(application) as client:
            seeded = client.put(
                "/editarr/api/settings/deployment", headers=headers, json={"port": 8999}
            )
            assert seeded.status_code == 200
            invalid = client.put(
                "/editarr/api/settings/deployment",
                headers=headers,
                json={"base_url": "not-a-url", "port": 9001},
            )
            assert invalid.status_code == 422
            assert invalid.json()["code"] == "deployment_configuration_invalid"
            assert DeploymentStore(tmp_path).read().port == 8999

            original_write = DeploymentStore._write_unlocked

            def fail_write(self, overrides):
                raise DeploymentConfigurationUnavailable("private path must not be exposed")

            monkeypatch.setattr(DeploymentStore, "_write_unlocked", fail_write)
            failed = client.put(
                "/editarr/api/settings/deployment", headers=headers, json={"port": 9001}
            )
            monkeypatch.setattr(DeploymentStore, "_write_unlocked", original_write)
        assert failed.status_code == 503
        assert failed.json() == {
            "code": "deployment_configuration_unavailable",
            "detail": "Deployment settings are temporarily unavailable",
        }
        assert "private path" not in failed.text
        assert DeploymentStore(tmp_path).read().port == 8999
    finally:
        engine.dispose()


def test_protected_update_rejects_missing_csrf_and_wrong_origin_without_mutating(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("EDITARR_BASE_URL", "https://editarr.example/editarr")
    application, _, configuration, session, engine = _application(monkeypatch, tmp_path)
    headers = _headers(configuration, session, "https://editarr.example")
    try:
        with TestClient(application) as client:
            missing_csrf = client.put(
                "/editarr/api/settings/deployment",
                headers={key: value for key, value in headers.items() if key != "X-CSRF-Token"},
                json={"port": 9001},
            )
            wrong_origin = client.put(
                "/editarr/api/settings/deployment",
                headers={**headers, "Origin": "https://attacker.example"},
                json={"port": 9001},
            )
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["code"] == "csrf_failed"
        assert wrong_origin.status_code == 403
        assert wrong_origin.json()["code"] == "origin_failed"
        assert DeploymentStore(tmp_path).read().port is None
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("method", "required"),
    [("external", "enabled"), ("forms", "disabled_for_local_addresses")],
)
def test_legacy_bypass_rows_never_open_deployment_settings(monkeypatch, tmp_path, method, required):
    monkeypatch.setenv("EDITARR_BASE_URL", "https://editarr.example/editarr")
    application, factory, _, _, engine = _application(monkeypatch, tmp_path)
    try:
        with factory() as db:
            db.merge(AuthSettingsRow(id=1, method=method, authentication_required=required))
            db.commit()
        with TestClient(application, client=("127.0.0.1", 50000)) as client:
            refused = client.put("/editarr/api/settings/deployment", json={"port": 9002})
        assert refused.status_code == 401
        assert refused.json()["code"] == "authentication_required"
        assert DeploymentStore(tmp_path).read().port is None
    finally:
        engine.dispose()
