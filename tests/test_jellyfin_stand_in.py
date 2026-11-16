"""The installed-release verifier's standard-library Jellyfin stand-in answers as setup's fake."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import scanner.api as scanner_api
import scanner.http.auth_api as auth_api
import scanner.security.auth_policy as auth_policy
from scanner.db import Base, get_db
from scanner.http import jellyfin_login_api, jellyfin_setup_api, scan_create_api
from scanner.integrations.jellyfin import JellyfinClient
from scanner.persistence.rows.db_auth import UserRow
from scanner.settings.config import Settings
from scanner.settings.deployment_config import DeploymentSnapshot, parse_base_url
from scripts import jellyfin_stand_in
from scripts.verify_installed_release import _FAKE_SCAN
from tests import jellyfin_setup_fake

_CLIENT = 'MediaBrowser Client="verifier", Device="verifier", DeviceId="1", Version="1"'


def _conversation(url: str) -> list[tuple[int, object]]:
    """Setup's calls in order, with each answer's status and its token-free body."""

    def call(method, path, token=None, **kwargs):
        authorization = _CLIENT + (f', Token="{token}"' if token else "")
        reply = httpx.request(
            method, url + path, headers={"Authorization": authorization}, **kwargs
        )
        return reply.status_code, (reply.json() if reply.content else None)

    def sign_in(password):
        return call(
            "POST", "/Users/AuthenticateByName", json={"Username": "Avery", "Pw": password}
        )

    answers = [call("GET", "/System/Info/Public"), sign_in("wrong")]
    status, body = sign_in(jellyfin_stand_in.ADMIN_PASSWORD)
    token = body.pop("AccessToken")
    answers.append((status, body))
    answers.append(call("POST", "/Auth/Keys", token, params={"app": "Editarr"}))
    status, keys = call("GET", "/Auth/Keys", token)
    [key] = keys["Items"]
    answers.append((status, key["AppName"]))
    answers.append(call("DELETE", f"/Auth/Keys/{key['AccessToken']}", token))
    answers.append(call("GET", "/Auth/Keys", token))
    answers.append(call("POST", "/Sessions/Logout", token))
    answers.append(call("GET", "/Auth/Keys", "never-issued"))
    return answers


def test_the_verifier_stand_in_answers_setup_as_the_setup_tests_fake():
    assert (jellyfin_stand_in.ADMIN, jellyfin_stand_in.ADMIN_PASSWORD) == (
        jellyfin_setup_fake.ADMIN,
        jellyfin_setup_fake.ADMIN_PASSWORD,
    )
    with jellyfin_stand_in.serve() as url:
        verifier = _conversation(url)
    with jellyfin_setup_fake.serve() as (_fake, url):
        fake = _conversation(url)

    assert verifier == fake


@pytest.fixture
def app(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'scanner.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    application = FastAPI()
    application.include_router(auth_api.router)
    application.include_router(jellyfin_login_api.router)
    application.include_router(scan_create_api.router)

    def test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = test_db
    monkeypatch.setenv("EDITARR_AUTH_COOKIE_SECURE", "false")
    monkeypatch.setattr(auth_api, "settings", Settings(data_dir=tmp_path))
    monkeypatch.setattr(
        jellyfin_setup_api, "setup_rate_limiter", type(jellyfin_setup_api.setup_rate_limiter)()
    )
    application.state.factory = factory
    yield application
    engine.dispose()


def test_editarr_sets_up_and_signs_in_through_the_verifier_stand_in(app):
    credentials = {
        "username": jellyfin_stand_in.ADMIN,
        "password": jellyfin_stand_in.ADMIN_PASSWORD,
    }
    with jellyfin_stand_in.serve() as url, TestClient(app) as client:
        setup = client.post("/api/auth/setup", json={"server_url": url, **credentials})
        client.cookies.clear()
        login = client.post("/api/auth/jellyfin/login", json=credentials)

    assert (setup.status_code, login.status_code) == (201, 200)
    assert login.json()["state"] == "authenticated"
    with app.state.factory() as db:
        [owner] = db.scalars(select(UserRow).where(UserRow.role == "owner"))
    assert owner.status == "active"


def test_editarr_queues_the_verifier_scan_of_the_stand_in_movie(app, tmp_path, monkeypatch):
    media = tmp_path / "fake-model-fixture.mp4"
    media.write_bytes(b"not decoded before the worker starts")
    monkeypatch.setattr(scanner_api, "_start_scan_queue_worker_if_needed", lambda: None)
    # Scans need the credential the auth policy records; setup alone does not.
    monkeypatch.setattr(auth_policy, "get_session_factory", lambda: app.state.factory)
    origin = parse_base_url("http://testserver")
    app.state.deployment_snapshot = DeploymentSnapshot(
        base_url=origin.base_url,
        public_origin=origin.public_origin,
        prefix=origin.prefix,
        listening_port=80,
        base_url_source="saved",
        port_source="saved",
        cli_port_override=None,
        cookie_secure=False,
        session_not_before=None,
        legacy_cookie_paths=(),
    )
    app.add_middleware(auth_policy.AuthPolicyMiddleware, configuration=Settings(data_dir=tmp_path))
    credentials = {
        "username": jellyfin_stand_in.ADMIN,
        "password": jellyfin_stand_in.ADMIN_PASSWORD,
    }
    with (
        jellyfin_stand_in.serve(media) as url,
        TestClient(app, headers={"Origin": "http://testserver"}) as client,
    ):
        setup = client.post("/api/auth/setup", json={"server_url": url, **credentials})
        csrf = {"X-CSRF-Token": setup.json()["csrf_token"]}
        queued = client.post("/api/scans", json=_FAKE_SCAN, headers=csrf)

    assert setup.status_code == 201
    assert queued.status_code == 201, queued.text
    assert isinstance(queued.json()["queue_id"], int)
    assert queued.json()["status"] == "queued"


def test_the_stand_in_movie_reads_through_editarrs_jellyfin_client(tmp_path):
    media = tmp_path / "fake-model-fixture.mp4"
    with jellyfin_stand_in.serve(media) as url:
        token = httpx.post(
            url + "/Users/AuthenticateByName",
            headers={"Authorization": _CLIENT},
            json={"Username": "Avery", "Pw": jellyfin_stand_in.ADMIN_PASSWORD},
        ).json()["AccessToken"]
        session = {"Authorization": _CLIENT + f', Token="{token}"'}
        httpx.post(url + "/Auth/Keys", headers=session, params={"app": "Editarr"})
        [key] = httpx.get(url + "/Auth/Keys", headers=session).json()["Items"]
        with JellyfinClient(url, key["AccessToken"]) as client:
            movie = client.item(jellyfin_stand_in.MOVIE_ID)
            [library] = client.libraries()

    assert (movie.item_type, movie.path, movie.duration_seconds) == ("Movie", str(media), 1.0)
    assert movie.library_id == library.id
    assert [version.path for version in movie.versions] == [str(media)]
