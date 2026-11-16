"""Adversarial coverage for the AUTH-09 global HTTP policy."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

import scanner.http.auth_api as auth_api
import scanner.security.auth_policy as auth_policy
from scanner.db import AuthSettingsRow, Base, get_db
from scanner.http.auth_api import router
from scanner.security.auth_origin import (
    OriginParseError,
    parse_origin_header,
    parse_public_origin,
    parse_referer_header,
)
from scanner.security.auth_repository import issue_session
from scanner.settings.config import Settings
from scanner.settings.deployment_config import DeploymentSnapshot, parse_base_url
from tests.jellyfin_setup_fake import ADMIN, FakeJellyfin, install, setup_body
from tests.multi_user_access_support import add_linked_owner


def _deployment_snapshot(base_url: str, *, cookie_secure: bool = True) -> DeploymentSnapshot:
    parsed = parse_base_url(base_url)
    return DeploymentSnapshot(
        base_url=parsed.base_url,
        public_origin=parsed.public_origin,
        prefix=parsed.prefix,
        listening_port=8000,
        base_url_source="saved",
        port_source="default",
        cli_port_override=None,
        cookie_secure=cookie_secure,
        session_not_before=None,
        legacy_cookie_paths=(),
    )


@pytest.fixture
def policy_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> Iterator[tuple[TestClient, dict[str, int], object]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    configuration = Settings(data_dir=tmp_path)
    add_linked_owner(db, "Admin")
    issued = issue_session(db, remember_me=False, configuration=configuration)
    db.close()
    monkeypatch.setattr(auth_policy, "get_session_factory", lambda: factory)
    application = FastAPI()
    application.state.deployment_snapshot = _deployment_snapshot("https://editarr.example")
    calls = {"get": 0, "mutate": 0}

    @application.get("/api/protected")
    def protected():
        calls["get"] += 1
        return {"ok": True}

    @application.post("/api/mutate")
    def mutate():
        calls["mutate"] += 1
        return {"ok": True}

    @application.post("/api/backups/jobs")
    def backup_job():
        calls["mutate"] += 1
        return {"ok": True}

    @application.put("/api/backups/schedule")
    def backup_schedule():
        calls["mutate"] += 1
        return {"ok": True}

    application.add_middleware(auth_policy.AuthPolicyMiddleware, configuration=configuration)
    with TestClient(application) as client:
        yield client, calls, issued
    engine.dispose()


def _session_headers(issued) -> dict[str, str]:
    return {"Cookie": f"editarr_session={issued.token}", "Origin": "https://editarr.example"}


def test_global_gate_blocks_anonymous_and_unknown_routes(policy_app):
    client, calls, _ = policy_app

    assert client.get("/api/protected").json()["code"] == "authentication_required"
    assert client.get("/docs").json()["code"] == "authentication_required"
    assert client.get("/not-a-route").json()["code"] == "authentication_required"
    assert calls == {"get": 0, "mutate": 0}


def test_database_maintenance_returns_retryable_refusal_without_entering_route(
    policy_app, monkeypatch
):
    from scanner.runtime.maintenance import MaintenanceUnavailable

    client, calls, _issued = policy_app

    def unavailable(*_args, **_kwargs):
        raise MaintenanceUnavailable("private maintenance diagnostic")

    monkeypatch.setattr(auth_policy, "setup_required", unavailable)
    response = client.get("/api/protected")
    assert response.status_code == 503
    assert response.json()["code"] == "database_maintenance"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "1"
    assert "private maintenance diagnostic" not in response.text
    assert calls == {"get": 0, "mutate": 0}


def test_cookie_authenticated_unsafe_requests_require_origin_then_exact_csrf(policy_app):
    client, calls, issued = policy_app
    headers = _session_headers(issued)

    missing = client.post("/api/mutate", headers=headers)
    assert missing.status_code == 403
    assert missing.json()["code"] == "csrf_failed"
    assert calls["mutate"] == 0

    wrong_origin = client.post(
        "/api/mutate",
        headers={
            **headers,
            "Origin": "https://attacker.example",
            "X-CSRF-Token": issued.csrf_token,
        },
    )
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["code"] == "origin_failed"
    assert calls["mutate"] == 0

    accepted = client.post("/api/mutate", headers={**headers, "X-CSRF-Token": issued.csrf_token})
    assert accepted.status_code == 200
    assert calls["mutate"] == 1


def test_backup_job_requires_session_origin_and_csrf(policy_app):
    client, calls, issued = policy_app
    headers = _session_headers(issued)
    assert client.post("/api/backups/jobs").status_code == 401
    assert client.post("/api/backups/jobs", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/backups/jobs", headers={**headers, "X-CSRF-Token": issued.csrf_token}
        ).status_code
        == 200
    )
    assert calls["mutate"] == 1


def _persist_legacy_modes(factory, method: str, required: str) -> None:
    """Write a pre-MU-07 authentication-mode row, which must no longer have any effect."""

    with factory() as database:
        database.merge(AuthSettingsRow(id=1, method=method, authentication_required=required))
        database.commit()


def test_backup_schedule_requires_session_and_csrf_under_a_legacy_external_row(policy_app):
    client, calls, issued = policy_app
    _persist_legacy_modes(auth_policy.get_session_factory(), "external", "enabled")
    headers = _session_headers(issued)
    assert client.put("/api/backups/schedule").status_code == 401
    assert client.put("/api/backups/schedule", headers=headers).status_code == 403
    assert client.put("/api/backups/schedule", headers={
        **headers, "X-CSRF-Token": issued.csrf_token,
    }).status_code == 200
    assert calls["mutate"] == 1


@pytest.mark.parametrize("port", [8000, 5173])
@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "10.0.0.64",
        "10.0.0.99",
        "[::1]",
        "editarr.freecountryusa.xyz",
        "other.internal",
        "arbitrary.example",
    ],
)
def test_direct_access_tracks_request_address_without_relaxing_csrf(policy_app, port, host):
    client, calls, issued = policy_app
    client.app.state.deployment_snapshot = replace(
        _deployment_snapshot(f"http://localhost:{port}", cookie_secure=False),
        base_url_source="local_default",
    )
    origin = f"http://{host}:{port}"
    request_url = f"http://127.0.0.1:{port}"
    headers = {
        "Cookie": f"editarr_session={issued.token}",
        "Origin": origin,
        "Host": f"{host}:{port}",
    }
    assert (
        client.get(f"{request_url}/api/protected", headers={"Host": headers["Host"]}).status_code
        == 401
    )
    missing = client.post(f"{request_url}/api/mutate", headers=headers)
    assert missing.status_code == 403
    assert missing.json()["code"] == "csrf_failed"
    accepted = client.post(
        f"{request_url}/api/mutate", headers={**headers, "X-CSRF-Token": issued.csrf_token}
    )
    assert accepted.status_code == 200
    rejected = client.post(
        f"{request_url}/api/mutate",
        headers={
            **headers,
            "Origin": f"http://10.0.0.100:{port}",
            "X-CSRF-Token": issued.csrf_token,
        },
    )
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "origin_failed"
    assert calls["mutate"] == 1


@pytest.mark.parametrize("authority", ["editarr.freecountryusa.xyz", "editarr.test:8443"])
def test_direct_access_https_proxy_preserves_origin_and_csrf_checks(policy_app, authority):
    client, calls, issued = policy_app
    client.app.state.deployment_snapshot = replace(
        _deployment_snapshot("http://localhost:5173", cookie_secure=False),
        base_url_source="local_default",
    )
    headers = {
        "Host": authority,
        "Origin": f"https://{authority}",
        "Sec-Fetch-Site": "same-origin",
        "Cookie": f"editarr_session={issued.token}",
        "X-CSRF-Token": issued.csrf_token,
    }
    accepted = client.post("http://127.0.0.1:8000/api/mutate", headers=headers)
    assert accepted.status_code == 200, accepted.text
    assert calls["mutate"] == 1
    for source in ("https://attacker.example", f"https://{authority.split(':')[0]}:9443"):
        rejected = client.post(
            "http://127.0.0.1:8000/api/mutate",
            headers={**headers, "Origin": source},
        )
        assert rejected.status_code == 403
        assert rejected.json()["code"] == "origin_failed"
    missing_csrf = client.post(
        "http://127.0.0.1:8000/api/mutate",
        headers={key: value for key, value in headers.items() if key != "X-CSRF-Token"},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["code"] == "csrf_failed"


@pytest.mark.parametrize("scheme,secure", [("http", False), ("https", True)])
def test_direct_proxy_login_cookie_uses_browser_transport(policy_app, scheme, secure):
    client, _, _ = policy_app
    client.app.state.deployment_snapshot = replace(
        _deployment_snapshot("http://localhost:5173", cookie_secure=False),
        base_url_source="local_default",
    )

    @client.app.post("/api/auth/jellyfin/login")
    def cookie_response(request: Request):
        response = auth_policy.JSONResponse({"ok": True})
        auth_api._set_session_cookie(
            response, "isolated-test-token", remember_me=False, request=request
        )
        return response

    response = client.post(
        "http://127.0.0.1:8000/api/auth/jellyfin/login",
        headers={
            "Host": "editarr.test",
            "Origin": f"{scheme}://editarr.test",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status_code == 200
    cookie = response.headers.get_list("set-cookie")[-1]
    assert ("Secure" in cookie) is secure
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


@pytest.mark.parametrize("authority", ["0.0.0.0:8000", "224.0.0.1:8000"])
def test_direct_access_rejects_wildcards_and_multicast(policy_app, authority):
    client, calls, issued = policy_app
    client.app.state.deployment_snapshot = replace(
        _deployment_snapshot("http://localhost:8000", cookie_secure=False),
        base_url_source="local_default",
    )
    origin = f"http://{authority}"
    response = client.post(
        f"{origin}/api/mutate",
        headers={
            "Origin": origin,
            "Cookie": f"editarr_session={issued.token}",
            "X-CSRF-Token": issued.csrf_token,
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_failed"
    assert calls["mutate"] == 0


@pytest.mark.parametrize("value", ["", "bad", "x" * 43, "A" * 42 + "=", "A" * 43 + ",x"])
def test_csrf_header_values_are_strict(policy_app, value):
    client, calls, issued = policy_app
    response = client.post(
        "/api/mutate", headers={**_session_headers(issued), "X-CSRF-Token": value}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"
    assert calls["mutate"] == 0


def test_raw_repeated_or_joined_csrf_header_is_rejected(policy_app):
    _, _, issued = policy_app
    session_factory = auth_policy.get_session_factory()
    db = session_factory()
    try:
        session = auth_policy.authenticate_session(db, issued.token)
        assert session is not None
        assert not auth_policy._csrf_is_valid(
            [(b"x-csrf-token", issued.csrf_token.encode())] * 2, session
        )
        assert not auth_policy._csrf_is_valid(
            [(b"x-csrf-token", f"{issued.csrf_token},other".encode())], session
        )
    finally:
        db.close()


def test_unsafe_source_origin_headless_exception_is_narrow():
    public = parse_public_origin("https://editarr.example")
    assert auth_policy._source_origin_is_valid([], public, unsafe=True, allow_headless=True)
    assert not auth_policy._source_origin_is_valid(
        [(b"sec-fetch-mode", b"cors")], public, unsafe=True, allow_headless=True
    )
    assert not auth_policy._source_origin_is_valid([], public, unsafe=True, allow_headless=False)
    assert not auth_policy._source_origin_is_valid(
        [(b"origin", b"null")], public, unsafe=True, allow_headless=True
    )
    assert not auth_policy._source_origin_is_valid(
        [(b"origin", b"https://editarr.example"), (b"origin", b"https://editarr.example")],
        public,
        unsafe=True,
        allow_headless=True,
    )


@pytest.mark.parametrize(
    ("value", "serialized"),
    [
        ("https://EDITARR.example:443", "https://editarr.example"),
        ("http://[2001:db8::1]:80", "http://[2001:db8::1]"),
        ("https://editarr.example/", "https://editarr.example"),
    ],
)
def test_public_origin_normalizes_only_approved_equivalences(value, serialized):
    assert parse_public_origin(value).serialized == serialized


@pytest.mark.parametrize(
    "value",
    [
        "https://editarr.example/path",
        "https://user@editarr.example",
        "https://editarr.example?x=1",
        "https://editarr.example#fragment",
        "https://editarr.example.",
        "https://bücher.example",
        "null",
        "https://editarr.example\\evil",
    ],
)
def test_public_origin_rejects_ambiguous_values(value):
    with pytest.raises(OriginParseError):
        parse_public_origin(value)


def test_source_origin_rejects_trailing_slash_and_preserves_exact_tuple():
    expected = parse_public_origin("https://editarr.example")
    assert parse_origin_header("https://EDITARR.example:443") == expected
    with pytest.raises(OriginParseError):
        parse_origin_header("https://editarr.example/")
    assert parse_referer_header("https://editarr.example/path?return=one") == expected


def test_application_route_inventory_requires_an_explicit_policy_classification():
    from scanner.api import app

    def iter_routes(route_collection):
        for route in route_collection:
            if hasattr(route, "path") and getattr(route, "methods", None) is not None:
                yield route
            elif hasattr(route, "original_router"):
                yield from iter_routes(route.original_router.routes)
            elif hasattr(route, "router"):
                yield from iter_routes(route.router.routes)

    public = {
        ("/api/auth/status", "GET"),
        ("/api/auth/setup", "POST"),
        ("/api/auth/jellyfin/login", "POST"),
        ("/api/auth/jellyfin/quick-connect", "GET"),
        ("/api/auth/jellyfin/quick-connect/initiate", "POST"),
        ("/api/auth/jellyfin/quick-connect/status", "GET"),
        ("/api/auth/jellyfin/quick-connect/complete", "POST"),
        ("/api/auth/jellyfin/quick-connect/cancel", "POST"),
    }
    protected_authentication = {
        ("/api/auth/logout", "POST"),
    }
    route_methods = {
        (route.path, method) for route in iter_routes(app.routes) for method in route.methods
    }

    assert public <= route_methods
    assert protected_authentication <= route_methods
    for removed in (
        ("/api/auth/security", "PUT"),
        ("/api/auth/login", "POST"),
        ("/api/auth/register", "POST"),
        ("/api/auth/account", "PUT"),
    ):
        assert removed not in route_methods
    for path, method in route_methods:
        if (path, method) in public | protected_authentication:
            continue
        assert path in (
            auth_policy.PROTECTED_ROUTE_PATHS
            | auth_policy.SETUP_RESTORE_PATHS
            | auth_policy.DOCUMENTATION_PATHS
        ), (
            path,
            method,
        )

    def paths(routes):
        for route in routes:
            if hasattr(route, "path"):
                yield route.path
            elif hasattr(route, "original_router"):
                yield from paths(route.original_router.routes)
            elif hasattr(route, "router"):
                yield from paths(route.router.routes)

    assert auth_policy.route_policy_inventory(list(paths(app.routes))) == set()


def test_access_matrix_preserves_the_setup_gate_and_rejects_session_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """Exercise every configured access state through the actual global middleware."""

    monkeypatch.setenv("EDITARR_AUTH_COOKIE_SECURE", "false")
    configuration = Settings(data_dir=tmp_path, auth_cookie_secure=False)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(auth_policy, "get_session_factory", lambda: factory)
    monkeypatch.setattr(auth_api, "settings", configuration)
    application = FastAPI()
    application.state.deployment_snapshot = _deployment_snapshot(
        "http://editarr.test", cookie_secure=False
    )
    calls = {"get": 0, "post": 0}

    @application.get("/api/protected")
    def protected_get():
        calls["get"] += 1
        return {"ok": True}

    @application.post("/api/protected")
    def protected_post():
        calls["post"] += 1
        return {"ok": True}

    def test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = test_db
    application.include_router(router)
    install(monkeypatch, FakeJellyfin(users={ADMIN: ("do-not-disclose-auth-canary", True)}))
    application.add_middleware(auth_policy.AuthPolicyMiddleware, configuration=configuration)
    original_session: str | None = None
    try:
        _persist_legacy_modes(factory, "external", "disabled_for_local_addresses")
        with TestClient(application, client=("127.0.0.1", 50000)) as client:
            assert client.get("/api/protected").status_code == 423
            assert client.post("/api/protected").status_code == 423
            assert calls == {"get": 0, "post": 0}

            setup = client.post(
                "/api/auth/setup",
                json=setup_body(password="do-not-disclose-auth-canary"),
            )
            assert setup.status_code == 201
            csrf_token = setup.json()["csrf_token"]
            original_session = client.cookies.get(configuration.auth_cookie_name)
            assert original_session is not None

            csrf_missing = client.post("/api/protected", headers={"Origin": "http://editarr.test"})
            assert csrf_missing.status_code == 403
            assert csrf_missing.json()["code"] == "csrf_failed"
            accepted = client.post(
                "/api/protected",
                headers={
                    "Origin": "http://editarr.test",
                    "X-CSRF-Token": csrf_token,
                },
            )
            assert accepted.status_code == 200
            assert calls == {"get": 0, "post": 1}

            logout = client.post(
                "/api/auth/logout",
                headers={
                    "Origin": "http://editarr.test",
                    "X-CSRF-Token": csrf_token,
                },
            )
            assert logout.status_code == 200

        replay_headers = {"Cookie": f"{configuration.auth_cookie_name}={original_session}"}
        for address in ("127.0.0.1", "::1", "10.0.0.2", "8.8.8.8"):
            with TestClient(application, client=(address, 50001)) as other_client:
                assert other_client.get("/api/protected").status_code == 401
                assert other_client.post("/api/protected").status_code == 401
                assert other_client.get("/api/protected", headers=replay_headers).status_code == 401
                status = other_client.get("/api/auth/status").json()
                assert status["state"] == "unauthenticated"
        assert calls == {"get": 0, "post": 1}
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("method", "required"),
    [("external", "enabled"), ("forms", "disabled_for_local_addresses")],
)
def test_no_legacy_mode_admits_a_sessionless_request_even_after_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path, method, required
):
    monkeypatch.setenv("EDITARR_AUTHENTICATION_METHOD", method)
    monkeypatch.setenv("EDITARR_AUTHENTICATION_REQUIRED", required)
    configuration = Settings(data_dir=tmp_path)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    add_linked_owner(db, "Admin")
    db.close()
    _persist_legacy_modes(factory, method, required)
    monkeypatch.setattr(auth_policy, "get_session_factory", lambda: factory)

    def fresh_application() -> FastAPI:
        application = FastAPI()
        application.state.deployment_snapshot = _deployment_snapshot("https://editarr.example")

        @application.get("/api/protected")
        def protected():
            return {"ok": True}

        application.add_middleware(auth_policy.AuthPolicyMiddleware, configuration=configuration)
        return application

    try:
        for _restart in range(2):
            for address in ("127.0.0.1", "::1"):
                with TestClient(fresh_application(), client=(address, 50000)) as client:
                    response = client.get("/api/protected")
                    assert response.status_code == 401
                    assert response.json()["code"] == "authentication_required"
    finally:
        engine.dispose()
