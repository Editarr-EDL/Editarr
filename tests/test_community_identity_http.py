"""MU-23 HTTP: own and named-account community connection routes, decided and audited.

`live_accounts` supplies owner 1, admins 2 and 3, and users 4 and 5 with sessions.
The app runs the real authentication middleware with the identity routes declared
evaluated, and `MockService` stands in for the community service.
"""

import json

import pytest

from scanner.http import community_identity_api, users_routes
from scanner.persistence.repository.repository_community import identity_ref
from scanner.security import community_identity as store
from scanner.security.private_identity_files import IdentityHome
from tests.community_identity_users_support import AUDIENCE, SERVICE, MockService, transport
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import (
    ADMIN,
    OWNER,
    PEER_ADMIN,
    PEER_USER,
    USER,
    live_accounts,
)
from tests.users_api_http_support import UsersApp, code

CHANGE = "community.identity.change"


@pytest.fixture
def accounts(tmp_path):
    live = live_accounts(tmp_path)
    yield live
    live.close()


def _serve(monkeypatch, accounts, service):
    configuration = accounts.configuration.model_copy(
        update={"community_service_url": SERVICE, "community_service_audience": AUDIENCE}
    )
    monkeypatch.setattr(community_identity_api, "settings", configuration)
    monkeypatch.setattr(community_identity_api, "service_transport", lambda: transport(service))
    client = serve_access_fixture(monkeypatch, accounts)
    client.app.include_router(community_identity_api.router)
    client.app.include_router(community_identity_api.users_router)
    return UsersApp(client, accounts)


@pytest.fixture
def service():
    return MockService()


@pytest.fixture
def app(monkeypatch, accounts, service):
    served = _serve(monkeypatch, accounts, service)
    yield served
    served.client.close()


def _home(app, user_id):
    with app.accounts.factory() as db:
        reference = identity_ref(db, user_id)
    return (
        None if reference is None else IdentityHome(app.accounts.configuration.data_dir, reference)
    )


def _pending_secret(app, user_id):
    identity = store.load_or_create_installation_identity(_home(app, user_id))
    return identity.client_state["pending"]["secret"]


def _changes(app):
    return [
        (outcome, actor, target, json.loads(details))
        for action, outcome, actor, target, details in app.audit()
        if action == CHANGE
    ]


def test_an_account_without_an_identity_reads_an_unconnected_status_and_gets_none(app):
    status = app.send("GET", "/api/community/connection/status", USER)
    recovery = app.send("GET", "/api/community/keys/recovery", USER)
    profile = app.send("GET", "/api/users/4/community/connection", ADMIN)

    assert status.status_code == 200 and status.headers["Cache-Control"] == "no-store"
    assert status.json()["identity"] is None and status.json()["connected"] is False
    assert recovery.json() == {
        "configured": True,
        "identity": None,
        "pending": None,
        "completed": None,
    }
    assert profile.status_code == 200 and profile.json()["identity"] is None
    assert code(app.send("DELETE", "/api/community/enrollment", USER)) == (409, "not_connected")
    assert _home(app, USER) is None


def test_each_account_enrolls_its_own_identity_without_exposing_its_secret(app, service):
    for user_id, state_id in ((USER, "a" * 64), (OWNER, "b" * 64)):
        started = app.ok("POST", "/api/community/enrollment/start", user_id)
        service.authorize(started["authorizationUrl"])
        secret = _pending_secret(app, user_id)
        assert secret not in json.dumps(started)
        body = {"state_id": state_id, "confirmed": True}
        completed = app.ok("POST", "/api/community/enrollment/complete", user_id, body)
        assert completed["connected"] is True
        assert secret not in json.dumps(completed)

    homes = {user_id: _home(app, user_id) for user_id in (OWNER, USER)}
    assert homes[OWNER].credential_ref != homes[USER].credential_ref
    identities = {
        user_id: store.load_or_create_installation_identity(home) for user_id, home in homes.items()
    }
    assert identities[OWNER].installation_id != identities[USER].installation_id
    assert identities[OWNER].contributor_id != identities[USER].contributor_id
    assert [entry[:3] for entry in _changes(app)] == [
        ("succeeded", USER, USER),
        ("succeeded", USER, USER),
        ("succeeded", OWNER, OWNER),
        ("succeeded", OWNER, OWNER),
    ]
    assert [entry[3] for entry in _changes(app)] == [
        {"change": "enrollment_start"},
        {"change": "enrollment_complete"},
    ] * 2


def test_admins_manage_ordinary_users_but_not_protected_accounts(app, service):
    started = app.ok("POST", "/api/users/4/community/enrollment/start", ADMIN)
    assert "authorizationUrl" in started
    assert _pending_secret(app, USER) not in json.dumps(started)
    assert app.ok("GET", "/api/users/4/community/connection/status", ADMIN)["pending"]
    assert _home(app, ADMIN) is None

    for target in (OWNER, PEER_ADMIN):
        refused = app.send("POST", f"/api/users/{target}/community/enrollment/start", ADMIN)
        assert code(refused) == (403, "protected_account")
        assert _home(app, target) is None or target == OWNER

    owner_managed = app.send("POST", "/api/users/2/community/enrollment/start", OWNER)
    assert owner_managed.status_code == 200 and _home(app, ADMIN) is not None

    assert [entry for entry in _changes(app) if entry[0] != "succeeded"] == [
        ("denied", ADMIN, OWNER, {"reason": "protected_account", "change": "enrollment_start"}),
        (
            "denied",
            ADMIN,
            PEER_ADMIN,
            {"reason": "protected_account", "change": "enrollment_start"},
        ),
    ]
    assert ("succeeded", ADMIN, USER, {"change": "enrollment_start"}) in _changes(app)
    assert ("succeeded", OWNER, ADMIN, {"change": "enrollment_start"}) in _changes(app)


def test_ordinary_users_manage_only_their_own_connection(app):
    assert code(app.send("POST", "/api/users/5/community/enrollment/start", USER)) == (
        404,
        "not_found",
    )
    assert code(app.send("GET", "/api/users/5/community/connection/status", USER)) == (
        404,
        "not_found",
    )
    assert code(app.send("POST", "/api/users/999/community/enrollment/start", USER)) == (
        404,
        "not_found",
    )
    assert app.send("POST", "/api/users/4/community/enrollment/start", USER).status_code == 200
    assert _home(app, PEER_USER) is None
    assert _changes(app) == [
        ("denied", USER, PEER_USER, {"reason": "not_found", "change": "enrollment_start"}),
        ("succeeded", USER, USER, {"change": "enrollment_start"}),
    ]


def test_the_user_directory_carries_no_community_material(app, service):
    started = app.ok("POST", "/api/community/enrollment/start", USER)
    service.authorize(started["authorizationUrl"])
    secret = _pending_secret(app, USER)
    body = {"state_id": "d" * 64, "confirmed": True}
    app.ok("POST", "/api/community/enrollment/complete", USER, body)
    home = _home(app, USER)
    identity = store.load_or_create_installation_identity(home)
    for router in users_routes.ROUTERS:
        app.client.app.include_router(router)

    material = {
        secret,
        home.credential_ref,
        identity.installation_id,
        identity.contributor_id,
        identity.key_id,
    }
    for actor, path in ((OWNER, "/api/users"), (OWNER, "/api/users/4"), (USER, "/api/users/4")):
        response = app.send("GET", path, actor)
        assert response.status_code == 200, path
        assert not [value for value in material if value in response.text], path


def test_a_confirmation_is_required_before_any_identity_work(app):
    body = {"state_id": "a" * 64, "confirmed": False}
    refused = app.send("POST", "/api/community/enrollment/complete", USER, body)
    assert code(refused) == (409, "confirmation_required")
    assert _home(app, USER) is None and _changes(app) == []


def test_disconnect_keeps_identity_files_and_reconnect_never_reenrolls(app, service, monkeypatch):
    started = app.ok("POST", "/api/community/enrollment/start", USER)
    service.authorize(started["authorizationUrl"])
    app.ok(
        "POST",
        "/api/community/enrollment/complete",
        USER,
        {"state_id": "a" * 64, "confirmed": True},
    )
    home = _home(app, USER)
    contributor = store.load_or_create_installation_identity(home).contributor_id
    before = {path.name: path.read_bytes() for path in home.root.iterdir() if path.is_file()}
    requests = list(service.requests)

    async def no_network(*args, **kwargs):
        pytest.fail("Disconnect or disconnected status contacted the community service")

    with monkeypatch.context() as guard:
        guard.setattr(community_identity_api.CommunityTransport, "request", no_network)
        for path in ("/api/community/connection", "/api/users/4/community/connection"):
            assert app.ok("DELETE", path, USER)["connected"] is False
        assert app.ok("GET", "/api/community/connection/status", USER)["connected"] is False
        assert app.ok("GET", "/api/community/connection", USER)["connected"] is False
    assert service.requests == requests
    assert {
        path.name: path.read_bytes() for path in home.root.iterdir() if path.is_file()
    } == before
    reconnected = app.ok("POST", "/api/community/enrollment/start", USER)
    assert reconnected["connected"] is True and "authorizationUrl" not in reconnected
    assert reconnected["profile"]["contributorId"] == contributor
    assert service.requests == requests
    assert all(
        path != "/v1/enrollments/complete" for path, _, _ in service.requests[len(requests) :]
    )
    assert _changes(app)[-3:-1] == [
        ("succeeded", USER, USER, {"change": "disconnect"}),
        ("succeeded", USER, USER, {"change": "disconnect"}),
    ]


def test_a_service_refusal_is_audited_without_its_payload(app, service):
    service.authorize(app.ok("POST", "/api/community/enrollment/start", USER)["authorizationUrl"])
    service.reject["/v1/enrollments/complete"] = (409, "enrollment_conflict")
    body = {"state_id": "c" * 64, "confirmed": True}
    failed = app.send("POST", "/api/community/enrollment/complete", USER, body)
    assert failed.json() == {"code": "enrollment_conflict", "retryable": False}
    assert _changes(app)[-1] == (
        "failed",
        USER,
        USER,
        {"change": "enrollment_complete", "reason": "service_rejected"},
    )


@pytest.mark.parametrize("key", [False, True], ids=["session", "api-key"])
def test_disconnect_refusals_and_named_management_follow_the_matrix(app, service, key):
    service.authorize(app.ok("POST", "/api/community/enrollment/start", USER)["authorizationUrl"])
    app.ok(
        "POST",
        "/api/community/enrollment/complete",
        USER,
        {"state_id": "b" * 64, "confirmed": True},
    )
    before = list(service.requests)
    hidden = app.send("DELETE", "/api/users/5/community/connection", USER, key=key)
    absent = app.send("DELETE", "/api/users/999/community/connection", USER, key=key)
    assert (hidden.status_code, hidden.content) == (absent.status_code, absent.content)
    assert code(hidden) == (404, "not_found")
    for target in (OWNER, PEER_ADMIN):
        assert code(
            app.send("DELETE", f"/api/users/{target}/community/connection", ADMIN, key=key)
        ) == (403, "protected_account")
    assert (
        app.send("DELETE", "/api/users/4/community/connection", ADMIN, key=key).status_code == 200
    )
    assert app.ok("GET", "/api/community/connection/status", USER)["connected"] is False
    assert ("succeeded", ADMIN, USER, {"change": "disconnect"}) in _changes(app)
    assert service.requests == before and _home(app, ADMIN) is None
    assert app.send("DELETE", "/api/community/connection", ADMIN, key=key).status_code == 200


def test_disconnect_holds_pending_enrollment_until_reconnect(app, service):
    started = app.ok("POST", "/api/community/enrollment/start", USER)
    service.authorize(started["authorizationUrl"])
    secret = _pending_secret(app, USER)
    app.ok("DELETE", "/api/community/connection", USER)
    body = {"state_id": "c" * 64, "confirmed": True}
    assert code(app.send("POST", "/api/community/enrollment/complete", USER, body)) == (
        409,
        "not_connected",
    )
    assert _pending_secret(app, USER) == secret and not service.requests
    resumed = app.ok("POST", "/api/community/enrollment/start", USER)
    assert resumed["authorizationUrl"] == started["authorizationUrl"]
    assert app.ok("POST", "/api/community/enrollment/complete", USER, body)["connected"] is True


def test_disconnected_missing_keys_never_generate_a_new_identity(app, service):
    started = app.ok("POST", "/api/community/enrollment/start", USER)
    service.authorize(started["authorizationUrl"])
    app.ok(
        "POST",
        "/api/community/enrollment/complete",
        USER,
        {"state_id": "d" * 64, "confirmed": True},
    )
    app.ok("DELETE", "/api/community/connection", USER)
    home = _home(app, USER)
    manifest = (home.root / "identity.json").read_bytes()
    (home.root / "current.key").unlink()
    for method, path in (
        ("GET", "/api/community/connection/status"),
        ("POST", "/api/community/enrollment/start"),
    ):
        assert code(app.send(method, path, USER)) == (409, "identity_incomplete")
    assert not (home.root / "current.key").exists()
    assert (home.root / "identity.json").read_bytes() == manifest
