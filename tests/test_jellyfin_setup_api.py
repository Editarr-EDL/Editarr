"""First-run setup through a Jellyfin administrator, against an in-process Jellyfin stand-in."""

from __future__ import annotations

import threading

import httpx
import idna
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import scanner.http.auth_api as auth_api
from scanner.db import Base, get_db
from scanner.http import jellyfin_setup_api
from scanner.integrations import jellyfin_setup
from scanner.persistence.repository.repository_settings import get_jellyfin_credentials
from scanner.persistence.rows.db_auth import UserProviderIdentityRow, UserRow
from scanner.security.jellyfin_accounts import owner_setup
from scanner.settings.config import Settings
from tests.jellyfin_setup_fake import (
    ADMIN_ID,
    MEMBER,
    MEMBER_PASSWORD,
    SERVER_URL,
    FakeJellyfin,
    install,
    setup_body,
)
from tests.jellyfin_user_boundary import PRIMARY


@pytest.fixture
def app(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'scanner.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    application = FastAPI()
    application.include_router(auth_api.router)

    def test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = test_db
    monkeypatch.setenv("EDITARR_AUTH_COOKIE_SECURE", "false")
    monkeypatch.setattr(auth_api, "settings", Settings(data_dir=tmp_path))
    application.state.factory = factory
    application.state.jellyfin = install(monkeypatch)
    yield application
    engine.dispose()


def _owners(app) -> list[UserRow]:
    with app.state.factory() as db:
        return list(db.scalars(select(UserRow).where(UserRow.role == "owner")))


def _assert_nothing_stored(app):
    assert _owners(app) == []
    assert app.state.jellyfin.editarr_keys() == []
    with app.state.factory() as db:
        assert get_jellyfin_credentials(db) is None
        assert db.scalar(select(func.count()).select_from(UserProviderIdentityRow)) == 0


def test_an_administrator_becomes_the_linked_owner_and_editarr_stores_its_minted_key(app):
    with TestClient(app) as client:
        setup = client.post("/api/auth/setup", json=setup_body())
        status = client.get("/api/auth/status").json()

    assert setup.status_code == 201
    assert setup.json()["state"] == status["state"] == "authenticated"
    assert status["jellyfin_user_id"] == ADMIN_ID
    [owner] = _owners(app)
    assert (owner.role, owner.status) == ("owner", "active")
    keys = app.state.jellyfin.editarr_keys()
    assert len(keys) == 1
    with app.state.factory() as db:
        assert get_jellyfin_credentials(db) == (SERVER_URL, keys[0])
        link = db.scalar(select(UserProviderIdentityRow))
    assert (link.user_id, link.server_id, link.provider_user_id) == (owner.id, PRIMARY, ADMIN_ID)
    assert app.state.jellyfin.logged_out == ["Avery"]


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        (setup_body(MEMBER, MEMBER_PASSWORD), 403, "jellyfin_not_administrator"),
        (setup_body(password="wrong password"), 401, "invalid_credentials"),
        (setup_body(server_url="ftp://jellyfin.test"), 400, "jellyfin_bad_url"),
        (setup_body(server_url="jellyfin.test"), 400, "jellyfin_bad_url"),
        (setup_body(server_url="http://[::1"), 400, "jellyfin_bad_url"),
        (setup_body(server_url=f"http://{'a' * 64}.test"), 400, "jellyfin_bad_url"),
        (setup_body(server_url="http://jellyfin.test:99999"), 400, "jellyfin_bad_url"),
        (setup_body(server_url="https://:8096"), 400, "jellyfin_bad_url"),
        (setup_body(server_url="http://xn--zz"), 400, "jellyfin_bad_url"),
        (setup_body(server_url="http://xn--a.example"), 400, "jellyfin_bad_url"),
        (setup_body(server_url="http://xn--ls8h.la"), 400, "jellyfin_bad_url"),
    ],
)
def test_a_refused_setup_names_why_and_stores_nothing(app, body, status, code):
    with TestClient(app) as client:
        response = client.post("/api/auth/setup", json=body)
        state = client.get("/api/auth/status").json()["state"]

    assert (response.status_code, response.json()["code"]) == (status, code)
    assert body["password"] not in response.text
    assert state == "setup_required"
    _assert_nothing_stored(app)


def test_an_address_httpx_cannot_encode_is_a_bad_url_not_a_server_error(app, monkeypatch):
    monkeypatch.setattr(jellyfin_setup, "normalize_server_url", lambda value: value)
    with TestClient(app) as client:
        response = client.post("/api/auth/setup", json=setup_body(server_url="http://xn--zz"))

    assert (response.status_code, response.json()["code"]) == (400, "jellyfin_bad_url")
    _assert_nothing_stored(app)


def test_a_non_administrator_is_signed_out_of_jellyfin(app):
    with TestClient(app) as client:
        client.post("/api/auth/setup", json=setup_body(MEMBER, MEMBER_PASSWORD))
    assert app.state.jellyfin.logged_out == [MEMBER]


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (httpx.ConnectError("refused"), "jellyfin_unreachable"),
        (httpx.ConnectTimeout("slow"), "jellyfin_unreachable"),
        (httpx.Response(404, text="<html>not here</html>"), "jellyfin_not_jellyfin"),
        (httpx.Response(200, json={"Id": "not-a-guid"}), "jellyfin_not_jellyfin"),
    ],
)
def test_an_address_that_is_not_a_reachable_jellyfin_is_refused(app, monkeypatch, failure, code):
    def answer(request):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(jellyfin_setup_api, "setup_transport", httpx.MockTransport(answer))
    with TestClient(app) as client:
        response = client.post("/api/auth/setup", json=setup_body())

    assert (response.status_code, response.json()["code"]) == (502, code)
    _assert_nothing_stored(app)


def test_a_key_jellyfin_will_not_create_refuses_setup(app):
    app.state.jellyfin.fail_create = True
    with TestClient(app) as client:
        response = client.post("/api/auth/setup", json=setup_body())

    assert (response.status_code, response.json()["code"]) == (502, "jellyfin_key_refused")
    _assert_nothing_stored(app)


def test_setup_after_an_owner_exists_is_refused_without_contacting_jellyfin(app, monkeypatch):
    with TestClient(app) as client:
        assert client.post("/api/auth/setup", json=setup_body()).status_code == 201
        contacted = []
        monkeypatch.setattr(
            jellyfin_setup_api,
            "setup_transport",
            httpx.MockTransport(lambda request: contacted.append(request) or httpx.Response(500)),
        )
        client.cookies.clear()
        again = client.post("/api/auth/setup", json=setup_body())

    assert (again.status_code, again.json()["code"]) == (409, "setup_already_complete")
    assert contacted == []
    assert len(_owners(app)) == 1 and len(app.state.jellyfin.editarr_keys()) == 1


def test_concurrent_setups_create_one_owner_and_one_key(app, monkeypatch):
    fake: FakeJellyfin = app.state.jellyfin
    both_signed_in = threading.Barrier(2, timeout=10)
    sign_in = fake._answer

    def answer(request):
        reply = sign_in(request)
        if request.url.path == "/Users/AuthenticateByName":
            both_signed_in.wait()
        return reply

    monkeypatch.setattr(jellyfin_setup_api, "setup_transport", httpx.MockTransport(answer))
    results = []

    def submit():
        with TestClient(app) as client:
            results.append(client.post("/api/auth/setup", json=setup_body()).status_code)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert sorted(results) == [201, 409]
    assert len(_owners(app)) == 1
    assert len(fake.editarr_keys()) == 1
    assert fake.logged_out == ["Avery", "Avery"]


def test_a_failure_after_minting_revokes_the_key(app, monkeypatch):
    def fail(*_args):
        raise RuntimeError("storage failed")

    monkeypatch.setattr(owner_setup, "adopt_unowned", fail)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/auth/setup", json=setup_body())

    assert response.status_code == 500
    _assert_nothing_stored(app)
    assert app.state.jellyfin.logged_out == ["Avery"]


def test_repeated_wrong_passwords_are_throttled_by_address(app):
    limit = auth_api.settings.auth_login_address_attempts
    with TestClient(app) as client:
        codes = [
            client.post("/api/auth/setup", json=setup_body(password=f"wrong {n}")).status_code
            for n in range(limit + 1)
        ]
    assert codes == [401] * limit + [429]


def test_wrong_passwords_from_one_address_do_not_lock_the_name_out_elsewhere(app):
    with TestClient(app, client=("203.0.113.9", 50000)) as attacker:
        codes = [
            attacker.post("/api/auth/setup", json=setup_body(password=f"wrong {n}")).status_code
            for n in range(12)
        ]
    with TestClient(app, client=("198.51.100.4", 50000)) as administrator:
        setup = administrator.post("/api/auth/setup", json=setup_body())

    assert set(codes) == {401}
    assert setup.status_code == 201
    assert len(_owners(app)) == 1


def _faulty(monkeypatch, fake: FakeJellyfin, fault):
    """Route setup through `fake`, letting `fault(request, answer)` replace any reply."""

    def answer(request):
        return fault(request, fake._answer)

    monkeypatch.setattr(jellyfin_setup_api, "setup_transport", httpx.MockTransport(answer))


def _key_calls(request) -> tuple[str, str]:
    return request.method, request.url.path


@pytest.mark.parametrize(
    "created",
    [httpx.Response(500), httpx.ReadTimeout("slow")],
    ids=["error-after-create", "timeout-after-create"],
)
def test_a_key_created_behind_a_failed_reply_is_revoked(app, monkeypatch, created):
    fake: FakeJellyfin = app.state.jellyfin

    def fault(request, answer):
        reply = answer(request)
        if _key_calls(request) == ("POST", "/Auth/Keys"):
            if isinstance(created, Exception):
                raise created
            return created
        return reply

    _faulty(monkeypatch, fake, fault)
    with TestClient(app) as client:
        response = client.post("/api/auth/setup", json=setup_body())

    assert (response.status_code, response.json()["code"]) == (502, "jellyfin_key_refused")
    _assert_nothing_stored(app)
    assert fake.logged_out == ["Avery"]


def test_every_new_editarr_key_is_revoked_when_the_new_key_is_ambiguous(app, monkeypatch):
    fake: FakeJellyfin = app.state.jellyfin

    def fault(request, answer):
        reply = answer(request)
        if _key_calls(request) == ("POST", "/Auth/Keys"):
            answer(request)
        return reply

    _faulty(monkeypatch, fake, fault)
    with TestClient(app) as client:
        response = client.post("/api/auth/setup", json=setup_body())

    assert (response.status_code, response.json()["code"]) == (502, "jellyfin_key_refused")
    _assert_nothing_stored(app)


def test_a_key_that_cannot_be_found_after_minting_is_reported(app, monkeypatch, caplog):
    fake: FakeJellyfin = app.state.jellyfin
    lists = []

    def fault(request, answer):
        if _key_calls(request) == ("GET", "/Auth/Keys"):
            lists.append(request)
            if len(lists) > 1:
                return httpx.Response(500)
        return answer(request)

    _faulty(monkeypatch, fake, fault)
    with TestClient(app) as client:
        response = client.post("/api/auth/setup", json=setup_body())
        state = client.get("/api/auth/status").json()["state"]

    assert (response.status_code, response.json()["code"]) == (502, "jellyfin_key_left_behind")
    assert "API Keys" in response.json()["detail"]
    assert state == "setup_required" and _owners(app) == []
    assert len(fake.editarr_keys()) == 1
    assert fake.editarr_keys()[0] not in caplog.text
    assert "may still hold an API key named Editarr" in caplog.text
    assert fake.logged_out == ["Avery"]


def test_a_key_jellyfin_will_not_delete_after_a_failure_is_reported(app, monkeypatch, caplog):
    fake: FakeJellyfin = app.state.jellyfin

    def fault(request, answer):
        if request.method == "DELETE":
            return httpx.Response(500)
        return answer(request)

    def fail(*_args):
        raise RuntimeError("storage failed")

    _faulty(monkeypatch, fake, fault)
    monkeypatch.setattr(owner_setup, "adopt_unowned", fail)
    with TestClient(app) as client:
        response = client.post("/api/auth/setup", json=setup_body())

    assert (response.status_code, response.json()["code"]) == (502, "jellyfin_key_left_behind")
    assert _owners(app) == [] and len(fake.editarr_keys()) == 1
    assert "storage failed" in caplog.text
    assert fake.logged_out == ["Avery"]


def test_a_sign_in_editarr_cannot_read_still_ends_the_jellyfin_session(app, monkeypatch):
    fake: FakeJellyfin = app.state.jellyfin

    def fault(request, answer):
        reply = answer(request)
        if request.url.path == "/Users/AuthenticateByName":
            body = reply.json()
            body["User"]["Id"] = "not-a-guid"
            return httpx.Response(200, json=body)
        return reply

    _faulty(monkeypatch, fake, fault)
    with TestClient(app) as client:
        response = client.post("/api/auth/setup", json=setup_body())

    assert (response.status_code, response.json()["code"]) == (502, "jellyfin_not_jellyfin")
    _assert_nothing_stored(app)
    assert fake.logged_out == ["Avery"]


def test_every_kind_of_refusal_counts_toward_the_address_limit(app, monkeypatch):
    fake: FakeJellyfin = app.state.jellyfin
    fake.fail_create = True
    limit = auth_api.settings.auth_login_address_attempts
    refusals = [
        (setup_body(server_url="ftp://jellyfin.test"), "jellyfin_bad_url"),
        (setup_body(server_url="http://xn--zz"), "jellyfin_bad_url"),
        (setup_body(MEMBER, MEMBER_PASSWORD), "jellyfin_not_administrator"),
        (setup_body(), "jellyfin_key_refused"),
        (setup_body(server_url="http://offline.test"), "jellyfin_unreachable"),
        (setup_body(server_url="http://not-jellyfin.test"), "jellyfin_not_jellyfin"),
        (setup_body(server_url="http://mangled.test"), "jellyfin_unreachable"),
    ]
    answer = fake._answer

    def route(request):
        if request.url.host == "offline.test":
            raise httpx.ConnectError("refused")
        if request.url.host == "mangled.test":
            raise idna.IDNAError("Invalid A-label")
        if request.url.host == "not-jellyfin.test":
            return httpx.Response(404)
        return answer(request)

    monkeypatch.setattr(jellyfin_setup_api, "setup_transport", httpx.MockTransport(route))
    with TestClient(app) as client:
        codes = [
            client.post("/api/auth/setup", json=refusals[n % len(refusals)][0]).json()["code"]
            for n in range(limit)
        ]
        fake.fail_create = False
        throttled = client.post("/api/auth/setup", json=setup_body())

    assert codes == [refusals[n % len(refusals)][1] for n in range(limit)]
    assert (throttled.status_code, throttled.json()["code"]) == (429, "login_throttled")
    assert int(throttled.headers["Retry-After"]) > 0
    _assert_nothing_stored(app)
