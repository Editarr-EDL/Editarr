from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner.db import AuthSettingsRow, Base, UserCapabilityOverrideRow, UserRow, get_db
from scanner.formats.export_layout import new_local_export_id
from scanner.http import community_api, community_identity_api, community_sharing_api
from scanner.security import auth_policy
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.request_access import install_access_errors
from scanner.security.auth_repository import issue_session
from scanner.settings.config import Settings
from tests.multi_user_access_support import _link, add_linked_owner, stand_in_identity
from tests.test_auth_policy import _deployment_snapshot


@pytest.fixture(params=["local", "external"])
def context(tmp_path, monkeypatch, request):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    configuration = Settings(
        data_dir=tmp_path, community_service_url=None, community_service_audience=None
    )
    with factory() as db:
        add_linked_owner(db, "Admin")
        issued = issue_session(db, remember_me=False, configuration=configuration)
        method = "external" if request.param == "external" else "forms"
        db.add(
            AuthSettingsRow(
                id=1, method=method, authentication_required="disabled_for_local_addresses"
            )
        )
        db.commit()
    monkeypatch.setattr(auth_policy, "get_session_factory", lambda: factory)
    monkeypatch.setattr(community_api, "settings", configuration)
    monkeypatch.setattr(community_identity_api, "settings", configuration)
    app = FastAPI(root_path="/editarr")
    install_access_errors(app)
    app.state.deployment_snapshot = _deployment_snapshot("https://editarr.example/editarr")
    app.add_middleware(auth_policy.AuthPolicyMiddleware, configuration=configuration)
    app.include_router(community_api.router)

    def database():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    with TestClient(app, base_url="https://editarr.example", client=("127.0.0.1", 50000)) as client:
        yield client, issued, factory, configuration
    engine.dispose()


def headers(issued):
    return {
        "Cookie": f"editarr_session={issued.token}",
        "Origin": "https://editarr.example",
        "X-CSRF-Token": issued.csrf_token,
    }


def test_privileged_routes_require_session_even_with_local_bypass(context):
    client, issued, *_ = context
    assert client.get("/editarr/api/community/connection").status_code == 401
    assert client.get("/editarr/api/community/connection/status").status_code == 401
    assert client.post("/editarr/api/community/enrollment/start").status_code == 401
    safe = client.get("/editarr/api/community/connection", headers=headers(issued))
    assert safe.status_code == 200
    assert safe.json() == {
        "configured": False,
        "connected": False,
        "profile": None,
        "pending": None,
    }
    local = client.get("/editarr/api/community/connection/status", headers=headers(issued))
    assert local.status_code == 200
    assert local.headers["Cache-Control"] == "no-store"
    assert local.json() == safe.json()


@pytest.mark.parametrize(
    ("endpoint", "audience", "expected"),
    [
        (None, None, "https://community.editarr.org"),
        ("https://community.example", "community.example", "https://community.example"),
        ("", "", None),
    ],
)
def test_connection_status_uses_service_defaults_or_environment_overrides(
    context, monkeypatch, endpoint, audience, expected
):
    client, issued, _, configuration = context
    for name, value in (
        ("EDITARR_COMMUNITY_SERVICE_URL", endpoint),
        ("EDITARR_COMMUNITY_SERVICE_AUDIENCE", audience),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    resolved = Settings(data_dir=configuration.data_dir)
    monkeypatch.setattr(community_identity_api, "settings", resolved)

    response = client.get("/editarr/api/community/connection/status", headers=headers(issued))
    assert response.status_code == 200
    assert response.json()["configured"] is (expected is not None)
    assert response.json()["connected"] is False
    assert response.json().get("serviceUrl") == expected


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "votes/revision-1"),
        ("POST", "votes/revision-1"),
        ("POST", "votes/revision-1/retry"),
        ("POST", "import-receipts/recover"),
        ("POST", "import-receipts/import-1/retry"),
        ("POST", "lookup"),
        ("POST", "downloads"),
        ("POST", "downloads/status"),
    ],
)
def test_retired_revision_routes_stay_protected_and_answer_gone(context, method, path):
    client, issued, *_ = context
    target = "/editarr/api/community/" + path
    kwargs = {"json": {}} if method == "POST" else {}
    assert client.request(method, target, **kwargs).status_code == 401
    valid = headers(issued)
    if method == "POST":
        missing = {key: value for key, value in valid.items() if key != "X-CSRF-Token"}
        assert (
            client.request(method, target, headers=missing, **kwargs).json()["code"]
            == "csrf_failed"
        )
    result = client.request(method, target, headers=valid, **kwargs)
    assert result.status_code == 410
    assert result.json() == {"error": "protocol_retired"}
    assert result.headers["cache-control"] == "no-store"


def test_start_and_confirmation_require_csrf_and_explicit_approval(context, monkeypatch):
    client, issued, *_ = context
    valid = headers(issued)
    missing = {name: value for name, value in valid.items() if name != "X-CSRF-Token"}
    assert (
        client.post("/editarr/api/community/enrollment/start", headers=missing).json()["code"]
        == "csrf_failed"
    )
    denied = client.post(
        "/editarr/api/community/enrollment/complete",
        headers=valid,
        json={"state_id": "b" * 64, "confirmed": False},
    )
    assert denied.status_code == 409
    assert denied.json()["code"] == "confirmation_required"
    assert (
        client.post("/editarr/api/community/enrollment/start", headers=valid).json()["code"]
        == "not_configured"
    )


def test_cross_origin_confirmation_is_rejected_before_handoff(context):
    client, issued, *_ = context
    response = client.post(
        "/editarr/api/community/enrollment/complete",
        headers=headers(issued) | {"Origin": "https://attacker.test"},
        json={"state_id": "b" * 64, "confirmed": True},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_failed"


@pytest.mark.parametrize("path", ["rotate", "rotation-retry", "rotation-status"])
def test_rotation_requires_real_session_csrf_and_confirmation(context, path):
    client, issued, *_ = context
    target = "/editarr/api/community/keys/" + path
    assert client.post(target, json={"confirmed": True}).status_code == 401
    valid = headers(issued)
    assert (
        client.post(
            target,
            headers={key: value for key, value in valid.items() if key != "X-CSRF-Token"},
            json={"confirmed": True},
        ).json()["code"]
        == "csrf_failed"
    )
    assert (
        client.post(
            target, headers=valid | {"Origin": "https://attacker.test"}, json={"confirmed": True}
        ).status_code
        == 403
    )
    if path != "rotation-status":
        denied = client.post(target, headers=valid, json={"confirmed": False})
        assert denied.json()["code"] == "confirmation_required"
    assert (
        client.post(target, headers=valid, json={"confirmed": True}).json()["code"]
        == "not_configured"
    )


@pytest.mark.parametrize("path", ["start", "complete", "check", "retry"])
def test_recovery_requires_real_session_csrf_and_confirmation(context, path):
    client, issued, *_ = context
    target = "/editarr/api/community/keys/recovery/" + path
    body = {"confirmed": True, "old_key_id": "sha256:" + "a" * 64, "state_id": "b" * 64}
    assert client.post(target, json=body).status_code == 401
    valid = headers(issued)
    assert (
        client.post(
            target,
            headers={name: value for name, value in valid.items() if name != "X-CSRF-Token"},
            json=body,
        ).json()["code"]
        == "csrf_failed"
    )
    assert (
        client.post(
            target, headers=valid | {"Origin": "https://attacker.test"}, json=body
        ).status_code
        == 403
    )
    if path != "check":
        assert (
            client.post(target, headers=valid, json=body | {"confirmed": False}).json()["code"]
            == "confirmation_required"
        )
    assert client.post(target, headers=valid, json=body).json()["code"] == "not_configured"
    assert client.get("/editarr/api/community/keys/recovery").status_code == 401
    assert (
        client.get("/editarr/api/community/keys/recovery", headers=valid).json()["configured"]
        is False
    )


@pytest.mark.parametrize(
    "method,path", [("GET", ""), ("DELETE", ""), ("POST", "/check"), ("POST", "/retry")]
)
def test_publication_requires_real_admin_csrf_and_confirmation(context, method, path):
    client, issued, *_ = context
    target = "/editarr/api/community/publications/publish-1" + path
    kwargs = {"json": {"confirmed": True}} if method == "POST" else {}
    assert client.request(method, target, **kwargs).status_code == 401
    valid = headers(issued)
    if method != "GET":
        missing = {name: value for name, value in valid.items() if name != "X-CSRF-Token"}
        assert (
            client.request(method, target, headers=missing, **kwargs).json()["code"]
            == "csrf_failed"
        )
        assert (
            client.request(
                method, target, headers=valid | {"Origin": "https://attacker.test"}, **kwargs
            ).status_code
            == 403
        )
    if path == "/retry":
        assert (
            client.post(target, headers=valid, json={"confirmed": False}).json()["code"]
            == "confirmation_required"
        )
    absent = client.request(method, target, headers=valid, **kwargs)
    assert absent.status_code == 404
    assert absent.json()["code"] == "not_found"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/sources/scan/scan-1"),
        ("POST", "/sources/scan/scan-1/preview"),
        ("POST", "/previews/preview-1/approve"),
        ("DELETE", "/previews/preview-1"),
    ],
)
def test_unconfigured_sharing_routes_require_admin_and_csrf(context, monkeypatch, method, path):
    client, issued, _, configuration = context
    monkeypatch.setattr(
        community_sharing_api, "settings", configuration.model_copy(
            update={"community_preview_bounds": None, "community_publication_request_bytes": None}
        ),
    )
    target = "/editarr/api/community/sharing" + path
    body = (
        {
            "json": {
                "name": "Public",
                "identity_confirmed": True,
                "version_choice": {"choice": "alias", "alias_id": "theatrical"},
            }
        }
        if path.endswith("preview")
        else ({"json": {"confirmed": True}} if method == "POST" else {})
    )
    assert client.request(method, target, **body).status_code == 401
    valid = headers(issued)
    if method != "GET":
        missing = {name: value for name, value in valid.items() if name != "X-CSRF-Token"}
        assert (
            client.request(method, target, headers=missing, **body).json()["code"] == "csrf_failed"
        )
        assert (
            client.request(
                method, target, headers=valid | {"Origin": "https://attacker.test"}, **body
            ).status_code
            == 403
        )
    assert (
        client.request(method, target, headers=valid, **body).json()["code"]
        == "preview_not_configured"
    )


def test_sharing_limits_allow_operator_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("EDITARR_COMMUNITY_PUBLICATION_REQUEST_BYTES", "100000")
    monkeypatch.setenv("EDITARR_COMMUNITY_PREVIEW_SOURCE_LIMIT", "2")
    monkeypatch.setenv(
        "EDITARR_COMMUNITY_PREVIEW_BOUNDS",
        '{"compressed_bytes":64000,"expanded_bytes":128000,"events":100,"text":512,"depth":8}',
    )
    configuration = Settings(data_dir=tmp_path)
    monkeypatch.setattr(community_sharing_api, "settings", configuration)

    assert configuration.community_publication_request_bytes == 100_000
    assert configuration.community_preview_source_limit == 2
    assert community_sharing_api.sharing_bounds().events == 100


@pytest.mark.parametrize(
    "method,path,capability",
    [
        ("POST", "import-receipts/recover", C.COMMUNITY_DOWNLOAD),
        ("POST", "import-receipts/import-1/retry", C.COMMUNITY_DOWNLOAD),
        ("POST", "downloads", C.COMMUNITY_DOWNLOAD),
        ("POST", "lookup", C.COMMUNITY_SEARCH),
        ("GET", "votes/revision-1", C.COMMUNITY_FEEDBACK_SUBMIT),
    ],
)
def test_retired_routes_keep_their_registered_capability_gate(context, method, path, capability):
    """A caller denied the route's registered capability gets 403, not the 410 stub."""
    client, _issued, factory, configuration = context
    now = datetime.now(timezone.utc)
    with factory() as db:
        user = UserRow(
            role="user",
            status="active",
            username="member",
            normalized_username="member",
            local_export_id=new_local_export_id().value,
        )
        db.add(user)
        db.flush()
        _link(db, user.id, stand_in_identity(user.id))
        db.add(
            UserCapabilityOverrideRow(
                user_id=user.id,
                capability=capability.value,
                effect="deny",
                updated_at=now,
            )
        )
        db.commit()
        member = issue_session(db, remember_me=False, user_id=user.id, configuration=configuration)
    target = "/editarr/api/community/" + path
    kwargs = {"json": {}} if method == "POST" else {}
    result = client.request(method, target, headers=headers(member), **kwargs)
    assert result.status_code == 403
    assert result.json()["code"] == "capability_denied"
    with factory() as db:
        db.query(UserCapabilityOverrideRow).delete()
        db.commit()
    assert client.request(method, target, headers=headers(member), **kwargs).status_code == 410
