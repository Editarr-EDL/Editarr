"""Prefix-preserving proxy routing and cookie scope coverage."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import scanner.http.auth_api as auth_api
import scanner.security.auth_policy as auth_policy
from scanner.api import PrefixRoutingMiddleware
from scanner.db import Base, get_db
from scanner.http.auth_api import router
from scanner.runtime.application_paths import application_url
from scanner.runtime.production_frontend import FrontendAssets, ProductionFrontendMiddleware
from scanner.security.auth_repository import issue_session
from scanner.settings.config import Settings
from scanner.settings.deployment_config import DeploymentSnapshot, parse_base_url
from tests.jellyfin_setup_fake import install, setup_body
from tests.multi_user_access_support import add_linked_owner


def test_direct_api_import_mounts_its_resolved_prefix(tmp_path):
    home = tmp_path / "home"
    runtime_temp = tmp_path / "runtime"
    home.mkdir()
    runtime_temp.mkdir()
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("EDITARR_")
    }
    environment.update(
        EDITARR_DATA_DIR=str(tmp_path / "data"),
        EDITARR_BASE_URL="https://editarr.test/media/editarr",
        HOME=str(home),
        TMPDIR=str(runtime_temp),
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from fastapi.testclient import TestClient\n"
            "from scanner.api import app\n"
            "from scanner.db import init_db\n"
            "init_db()\n"
            "client = TestClient(app)\n"
            "assert app.root_path == '/media/editarr'\n"
            "assert client.get('/media/editarr/api/auth/status').status_code == 200\n"
            "assert client.get('/media/editarr/docs').status_code == 423\n"
            "assert client.get('/api/auth/status').status_code == 404\n",
        ],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _snapshot(
    base_url: str = "http://editarr.test/editarr",
    *,
    cutoff: datetime | None = None,
    legacy_paths: tuple[str, ...] = (),
) -> DeploymentSnapshot:
    parsed = parse_base_url(base_url)
    return DeploymentSnapshot(
        base_url=parsed.base_url,
        public_origin=parsed.public_origin,
        prefix=parsed.prefix,
        listening_port=8000,
        base_url_source="saved",
        port_source="default",
        cli_port_override=None,
        cookie_secure=False,
        session_not_before=cutoff,
        legacy_cookie_paths=legacy_paths,
    )


def _application(
    monkeypatch,
    tmp_path,
    snapshot: DeploymentSnapshot,
    *,
    frontend_assets: FrontendAssets | None = None,
):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setenv("EDITARR_AUTH_COOKIE_SECURE", "false")
    configuration = Settings(data_dir=tmp_path)
    monkeypatch.setattr(auth_policy, "get_session_factory", lambda: factory)
    monkeypatch.setattr(auth_api, "settings", configuration)

    application = FastAPI()
    application.state.deployment_snapshot = snapshot
    application.state.frontend_assets = frontend_assets
    application.root_path = snapshot.prefix

    def test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = test_db
    application.include_router(router)
    install(monkeypatch)

    @application.get("/api/protected")
    def protected():
        return {"ok": True}

    @application.post("/api/slashed/")
    def slashed():
        return {"ok": True}

    application.add_middleware(auth_policy.AuthPolicyMiddleware, configuration=configuration)
    application.add_middleware(ProductionFrontendMiddleware)
    application.add_middleware(PrefixRoutingMiddleware)
    return application, factory, configuration, engine


def _write_frontend_assets(root):
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        '<script id="editarr-bootstrap" type="application/json" '
        'data-editarr-bootstrap="development">{"version":1,"basePath":"/",'
        '"apiBasePath":"/api"}</script><script type="module" '
        'src="./assets/index-abcdefgh.js"></script>',
        encoding="utf-8",
    )
    (root / "assets" / "index-abcdefgh.js").write_text("export {}", encoding="utf-8")
    return FrontendAssets.from_directory(root)


def test_static_shell_is_public_but_real_auth_policy_keeps_api_and_docs_gated(
    monkeypatch, tmp_path
):
    frontend_assets = _write_frontend_assets(tmp_path / "frontend")
    application, factory, configuration, engine = _application(
        monkeypatch,
        tmp_path,
        _snapshot(),
        frontend_assets=frontend_assets,
    )
    try:
        with TestClient(application) as client:
            shell = client.get("/editarr/")
            assert shell.status_code == 200
            assert '"basePath":"/editarr"' in shell.text
            assert client.get("/editarr/assets/index-abcdefgh.js").status_code == 200
            assert client.get("/editarr/api/protected").status_code == 423
            assert client.get("/editarr/docs").status_code == 423
            assert client.get("/editarr/assets/missing.js").status_code == 404
    finally:
        engine.dispose()


def test_prefixed_routes_reject_root_aliases_and_keep_docs_protected(monkeypatch, tmp_path):
    application, factory, configuration, engine = _application(monkeypatch, tmp_path, _snapshot())
    try:
        with TestClient(application) as client:
            assert client.get("/api/auth/status").status_code == 404
            assert client.get("/api/protected").status_code == 404
            assert client.get("/editarr/api/auth/status").json()["state"] == "setup_required"
            assert client.get("/editarr/docs").status_code == 423

            setup = client.post(
                "/editarr/api/auth/setup",
                headers={"Origin": "http://editarr.test"},
                json=setup_body(),
            )
            assert setup.status_code == 201
            schema = client.get("/editarr/openapi.json")
            assert schema.status_code == 200
            assert schema.json()["servers"] == [{"url": "/editarr"}]
            assert client.get("/openapi.json").status_code == 404
            assert client.get("/editarr/docs").status_code == 200
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("base_url", "status_path", "root_alias_status"),
    [
        ("http://editarr.test/", "/api/auth/status", None),
        ("http://editarr.test/media/editarr", "/media/editarr/api/auth/status", 404),
    ],
)
def test_root_and_nested_prefix_route_assembly(
    monkeypatch, tmp_path, base_url, status_path, root_alias_status
):
    application, factory, configuration, engine = _application(
        monkeypatch, tmp_path, _snapshot(base_url)
    )
    try:
        with TestClient(application) as client:
            assert client.get(status_path).status_code == 200
            if root_alias_status is not None:
                assert client.get("/api/auth/status").status_code == root_alias_status
    finally:
        engine.dispose()


def test_cookie_paths_and_cutoff_are_fixed_by_the_active_snapshot(monkeypatch, tmp_path):
    cutoff = datetime.now(UTC)
    snapshot = _snapshot(cutoff=cutoff, legacy_paths=("/old", "/"))
    application, factory, configuration, engine = _application(monkeypatch, tmp_path, snapshot)
    try:
        with TestClient(application) as client:
            login = client.post(
                "/editarr/api/auth/setup",
                headers={"Origin": "http://editarr.test"},
                json=setup_body(),
            )
            assert login.status_code == 201, login.text
            db = factory()
            try:
                old_session = issue_session(
                    db,
                    remember_me=False,
                    now=cutoff - timedelta(seconds=1),
                    configuration=configuration,
                )
            finally:
                db.close()
            old_headers = {
                "Cookie": f"{configuration.auth_cookie_name}={old_session.token}",
                "Origin": "http://editarr.test",
            }
            assert client.get("/editarr/api/protected", headers=old_headers).status_code == 401
            assert client.get("/editarr/api/auth/status", headers=old_headers).json()["state"] == (
                "unauthenticated"
            )
            assert (
                client.get(
                    "/editarr/api/protected",
                    headers={
                        "Cookie": (
                            f"{configuration.auth_cookie_name}=one; "
                            f"{configuration.auth_cookie_name}=two"
                        )
                    },
                ).status_code
                == 401
            )

            cookies = login.headers.get_list("set-cookie")
            assert {"Path=/", "Path=/old", "Path=/editarr"} <= {
                next(part for part in cookie.split("; ") if part.startswith("Path="))
                for cookie in cookies
            }
            assert any("Max-Age=0" in cookie and "Path=/editarr" in cookie for cookie in cookies)
            assert any(
                "Max-Age=0" not in cookie and "Path=/editarr" in cookie for cookie in cookies
            )

            csrf_token = login.json()["csrf_token"]
            logout = client.post(
                "/editarr/api/auth/logout",
                headers={"Origin": "http://editarr.test", "X-CSRF-Token": csrf_token},
            )
            assert logout.status_code == 200
            assert {"Path=/", "Path=/old", "Path=/editarr"} <= {
                next(part for part in cookie.split("; ") if part.startswith("Path="))
                for cookie in logout.headers.get_list("set-cookie")
            }
    finally:
        engine.dispose()


def test_canonical_redirects_are_prefix_aware_relative_and_method_preserving(monkeypatch, tmp_path):
    application, factory, configuration, engine = _application(monkeypatch, tmp_path, _snapshot())
    try:
        db = factory()
        try:
            add_linked_owner(db, "Admin")
            issued = issue_session(db, remember_me=False, configuration=configuration)
        finally:
            db.close()
        headers = {
            "Cookie": f"{configuration.auth_cookie_name}={issued.token}",
            "Origin": "http://editarr.test",
            "X-CSRF-Token": issued.csrf_token,
        }
        with TestClient(application) as client:
            entry = client.post("/editarr?return=one", follow_redirects=False)
            assert entry.status_code == 307
            assert entry.headers["location"] == "/editarr/?return=one"
            slash = client.post(
                "/editarr/api/slashed?return=one", headers=headers, follow_redirects=False
            )
            assert slash.status_code == 307
            assert slash.headers["location"] == "/editarr/api/slashed/?return=one"
    finally:
        engine.dispose()


def test_application_urls_never_double_the_prefix():
    snapshot = _snapshot("https://editarr.test/media/editarr")
    assert application_url(snapshot, "/api/library-items/item/image") == (
        "/media/editarr/api/library-items/item/image"
    )
