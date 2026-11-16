"""Production frontend shell/static serving coverage."""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from scanner.api import PrefixRoutingMiddleware
from scanner.runtime.production_frontend import (
    FrontendAssets,
    FrontendAssetsError,
    ProductionFrontendMiddleware,
    packaged_frontend_directory,
)
from scanner.security.auth_policy import PUBLIC_STATIC_ROUTE_PATHS, public_static_route_inventory
from scanner.settings.deployment_config import DeploymentSnapshot, parse_base_url


def test_packaged_frontend_path_remains_at_the_scanner_package_root():
    assert packaged_frontend_directory() == Path(__file__).resolve().parents[1] / "scanner/frontend"


class ProtectedDataMiddleware(BaseHTTPMiddleware):
    """A small stand-in proving the static layer is the only auth bypass."""

    async def dispatch(self, request: Request, call_next):
        return JSONResponse({"detail": "authentication required"}, status_code=401)


def _snapshot(base_url: str = "https://editarr.test/editarr") -> DeploymentSnapshot:
    parsed = parse_base_url(base_url)
    return DeploymentSnapshot(
        base_url=parsed.base_url,
        public_origin=parsed.public_origin,
        prefix=parsed.prefix,
        listening_port=8000,
        base_url_source="saved",
        port_source="default",
        cli_port_override=None,
        cookie_secure=True,
        session_not_before=None,
        legacy_cookie_paths=(),
    )


def _write_frontend(root, *, marker: bool = True) -> None:
    (root / "assets").mkdir(parents=True)
    bootstrap = (
        '<script id="editarr-bootstrap" type="application/json" '
        'data-editarr-bootstrap="development">{"version":1,"basePath":"/",'
        '"apiBasePath":"/api"}</script>'
        if marker
        else ""
    )
    (root / "index.html").write_text(
        "<!doctype html><html><head>"
        f"{bootstrap}"
        '<link rel="stylesheet" href="./assets/index-abcdefgh.css">'
        "</head><body><div id=\"root\"></div>"
        '<script type="module" src="./assets/index-abcdefgh.js"></script>'
        "</body></html>",
        encoding="utf-8",
    )
    (root / "assets" / "index-abcdefgh.js").write_text("console.log('ok')", encoding="utf-8")
    (root / "assets" / "index-abcdefgh.css").write_text("body{}", encoding="utf-8")
    (root / "favicon.ico").write_bytes(b"icon")


def _application(tmp_path, snapshot: DeploymentSnapshot):
    asset_root = tmp_path / "frontend"
    _write_frontend(asset_root)
    application = FastAPI()
    application.state.deployment_snapshot = snapshot
    application.state.frontend_assets = FrontendAssets.from_directory(asset_root)
    application.root_path = snapshot.prefix

    @application.get("/api/known")
    def known_api():
        return {"ok": True}

    # Mirror production ordering: Prefix is outermost, static is public immediately
    # inside it, and every other route reaches the auth/data layer.
    application.add_middleware(ProtectedDataMiddleware)
    application.add_middleware(ProductionFrontendMiddleware)
    application.add_middleware(PrefixRoutingMiddleware)
    return application, asset_root


@pytest.mark.parametrize("prefix", ["/café", "/a&b", "/a<b>"])
def test_bootstrap_json_escapes_are_inserted_literally(tmp_path, prefix):
    _write_frontend(tmp_path)
    frontend = FrontendAssets.from_directory(tmp_path)
    # Defense in depth: preserve inert JSON even if a future parser broadens syntax.
    rendered = frontend.entry_html(replace(_snapshot(), prefix=prefix)).decode()
    match = re.search(
        r'<script id="editarr-bootstrap" type="application/json">(.*?)</script>', rendered
    )
    assert match is not None
    assert json.loads(match.group(1))["basePath"] == prefix
    assert all(character not in match.group(1) for character in "&<>")


def test_entry_assets_and_bootstrap_are_public_under_the_active_prefix(tmp_path):
    application, _ = _application(tmp_path, _snapshot())
    with TestClient(application) as client:
        entry = client.get("/editarr/")
        assert entry.status_code == 200
        assert entry.headers["cache-control"] == "no-store"
        bootstrap = re.search(
            r'<script id="editarr-bootstrap" type="application/json">(.*?)</script>', entry.text
        )
        assert bootstrap is not None
        assert json.loads(bootstrap.group(1)) == {
            "version": 1,
            "basePath": "/editarr",
            "apiBasePath": "/editarr/api",
        }
        script = client.get("/editarr/assets/index-abcdefgh.js")
        assert script.text == "console.log('ok')"
        assert script.headers["content-type"].startswith("text/javascript")
        assert (
            script.headers["cache-control"]
            == "public, max-age=31536000, immutable"
        )
        assert client.get("/editarr/favicon.ico").headers["cache-control"] == "no-cache"
        assert client.get("/editarr/api/known").status_code == 401


def test_static_requests_are_canonical_narrow_and_do_not_fall_back_to_html(tmp_path):
    application, asset_root = _application(tmp_path, _snapshot())
    (asset_root / "secret.txt").write_text("private", encoding="utf-8")
    try:
        os.symlink(asset_root / "secret.txt", asset_root / "assets" / "linked.js")
    except OSError:  # pragma: no cover - platforms/filesystems without symlink support
        pytest.skip("symlinks are unavailable")
    with TestClient(application) as client:
        redirect = client.get("/editarr?x=1", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["location"] == "/editarr/?x=1"
        assert client.get("/api/known").status_code == 404
        assert client.get("/assets/index-abcdefgh.js").status_code == 404
        assert client.get("/editarr/api/unknown").status_code == 401
        assert client.get("/editarr/docs").status_code == 401
        assert client.get("/editarr/assets/").status_code == 404
        assert client.get("/editarr/assets/missing.js").status_code == 404
        assert client.get("/editarr/assets/linked.js").status_code == 404
        assert client.get("/editarr/assets/%2e%2e/secret.txt").status_code == 404
        assert client.get("/editarr/not-a-hash-route").status_code == 401
        assert client.post("/editarr/").status_code == 405
        assert client.post("/editarr/assets/index-abcdefgh.js").status_code == 405
        head = client.head("/editarr/")
        assert head.status_code == 200
        assert head.content == b""
        assert int(head.headers["content-length"]) > 0


def test_root_deployment_and_invalid_packaged_assets(tmp_path):
    root_snapshot = _snapshot("http://editarr.test/")
    application, _ = _application(tmp_path, root_snapshot)
    with TestClient(application) as client:
        assert client.get("/").status_code == 200
        assert client.get("/assets/index-abcdefgh.css").status_code == 200
        assert client.get("/editarr/").status_code == 401

    missing = tmp_path / "missing"
    with pytest.raises(FrontendAssetsError, match="--api-only"):
        FrontendAssets.from_directory(missing)
    malformed = tmp_path / "malformed"
    _write_frontend(malformed, marker=False)
    with pytest.raises(FrontendAssetsError, match="bootstrap marker"):
        FrontendAssets.from_directory(malformed)


def test_public_static_route_inventory_is_explicit_and_narrow():
    assert public_static_route_inventory(PUBLIC_STATIC_ROUTE_PATHS) == set()
    assert public_static_route_inventory(["/api/auth/status"]) == {"/api/auth/status"}
