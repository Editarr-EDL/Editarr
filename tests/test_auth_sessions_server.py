"""MU-07 concurrent Jellyfin sign-in against the production app over real loopback HTTP.

The app runs in its own process from this checkout in verification mode on a
fresh data directory seeded by `live_accounts` (`mu04-access-builders-3`), with
the owner, an admin, and a user linked to Jellyfin users of the MU-08
`JellyfinBoundary`, a loopback stand-in for Jellyfin. The OS assigns the loopback
port. Threads release each round together, so the sign-ins and requests of
different users overlap on the server.
"""

from __future__ import annotations

import http.client
import json
import os
import secrets
import select
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete

from scanner.db import JellyfinSettingsRow, UserProviderIdentityRow
from tests.jellyfin_user_boundary import IDS, PRIMARY, JellyfinBoundary
from tests.multi_user_access_support import ADMIN, OWNER, USER, live_accounts

_ROOT = Path(__file__).resolve().parents[1]
_LAUNCH = """
import json, socket, sys, uvicorn
from scanner import api

class Ready(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            print(json.dumps({"port": sockets[0].getsockname()[1]}), flush=True)

with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    Ready(uvicorn.Config(api.app, log_level="error")).run(sockets=[listener])
"""
_NAMES = {OWNER: "Avery", ADMIN: "Blake", USER: "Casey"}
_PASSWORDS = {IDS[name]: f"jf-{name.lower()}-password" for name in IDS}
_CREDENTIALS = {user_id: (name, _PASSWORDS[IDS[name]]) for user_id, name in _NAMES.items()}


def _link_to(accounts, url: str, api_key: str) -> None:
    now = datetime.now(UTC)
    with accounts.factory() as db:
        db.add(JellyfinSettingsRow(id=1, server_url=url, api_key=api_key))
        db.execute(delete(UserProviderIdentityRow))
        for user_id, name in _NAMES.items():
            db.add(
                UserProviderIdentityRow(
                    user_id=user_id,
                    server_id=PRIMARY,
                    provider_user_id=IDS[name],
                    link_state="verified",
                    last_verified_at=now,
                )
            )
        db.commit()


@pytest.fixture
def jellyfin():
    with JellyfinBoundary(secrets.token_urlsafe(24), _PASSWORDS) as boundary:
        yield boundary


@pytest.fixture
def server(tmp_path, jellyfin):
    data = tmp_path / "data"
    data.mkdir()
    accounts = live_accounts(data)
    _link_to(accounts, jellyfin.url, jellyfin.api_key)
    owner_key = accounts.key(OWNER)
    accounts.close()
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("EDITARR_")
    }
    environment.update(
        PYTHONPATH=str(_ROOT),
        EDITARR_DATA_DIR=str(data),
        EDITARR_LOG_DIR=str(tmp_path / "logs"),
    )
    process = subprocess.Popen(
        [sys.executable, "-c", _LAUNCH],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=(tmp_path / "server.log").open("wb"),
        start_new_session=True,
    )
    try:
        ready, _, _ = select.select([process.stdout], [], [], 60)
        assert ready, (tmp_path / "server.log").read_text()
        line = process.stdout.readline()
        assert line, (tmp_path / "server.log").read_text()
        port = json.loads(line)["port"]
        _await_startup_sync(port, owner_key)
        yield port
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=30)


def _await_startup_sync(port: int, owner_key: dict) -> None:
    """Wait out the sync startup begins at once; its newer read refuses an older sign-in."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status, _, body = _request(port, "GET", "/api/jellyfin/sync", headers=owner_key)
        assert status == 200, body
        if not body["running"] and body["last_run"] is not None:
            return
        time.sleep(0.05)
    raise AssertionError("The startup Jellyfin sync did not finish")


def _request(port: int, method: str, path: str, headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        connection.request(
            method,
            path,
            body=payload,
            headers={
                "Origin": f"http://127.0.0.1:{port}",
                **({"Content-Type": "application/json"} if payload else {}),
                **(headers or {}),
            },
        )
        response = connection.getresponse()
        return response.status, response.getheaders(), json.loads(response.read() or b"null")
    finally:
        connection.close()


def _together(actions):
    """Run each action on its own thread, released at once; return results by key."""

    start = threading.Barrier(len(actions))
    results, errors = {}, []

    def run(key, action):
        try:
            start.wait(timeout=30)
            results[key] = action()
        except BaseException as error:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(error)

    threads = [threading.Thread(target=run, args=item) for item in actions.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    return results


def _session(headers) -> str:
    cookies = [value for name, value in headers if name.lower() == "set-cookie"]
    issued = [
        value
        for value in cookies
        if value.startswith("editarr_session=") and "Max-Age=0" not in value
    ]
    assert len(issued) == 1, cookies
    return issued[0].split(";", 1)[0]


def test_concurrent_sign_ins_keep_identity_cookies_sessions_and_logout_separate(server):
    port = server
    for _round in range(3):
        signed_in = _together(
            {
                user_id: lambda username=username, password=password: _request(
                    port,
                    "POST",
                    "/api/auth/jellyfin/login",
                    body={"username": username, "password": password},
                )
                for user_id, (username, password) in _CREDENTIALS.items()
            }
        )
        browsers = {}
        for user_id, (status, headers, body) in signed_in.items():
            assert status == 200, body
            assert body["access"]["principal"]["user_id"] == user_id
            browsers[user_id] = {"Cookie": _session(headers), "X-CSRF-Token": body["csrf_token"]}
        assert len({browser["Cookie"] for browser in browsers.values()}) == len(_CREDENTIALS)
        assert len({browser["X-CSRF-Token"] for browser in browsers.values()}) == len(_CREDENTIALS)

        statuses = _together(
            {
                user_id: lambda browser=browser: _request(
                    port, "GET", "/api/auth/status", headers={"Cookie": browser["Cookie"]}
                )
                for user_id, browser in browsers.items()
            }
        )
        for user_id, (status, _headers, body) in statuses.items():
            assert (status, body["state"]) == (200, "authenticated")
            assert body["access"]["principal"]["user_id"] == user_id
            assert body["csrf_token"] == browsers[user_id]["X-CSRF-Token"]

        after_logout = _together(
            {
                "logout": lambda: _request(
                    port, "POST", "/api/auth/logout", headers=browsers[USER]
                ),
                "owner": lambda: _request(
                    port, "GET", "/api/settings/deployment", headers=browsers[OWNER]
                ),
                "admin": lambda: _request(
                    port, "GET", "/api/auth/status", headers={"Cookie": browsers[ADMIN]["Cookie"]}
                ),
            }
        )
        assert after_logout["logout"][0] == 200
        assert after_logout["owner"][0] == 200
        assert after_logout["admin"][2]["access"]["principal"]["user_id"] == ADMIN

        gone = _request(
            port, "GET", "/api/auth/status", headers={"Cookie": browsers[USER]["Cookie"]}
        )
        assert gone[2]["state"] == "unauthenticated"
        for user_id in (OWNER, ADMIN):
            kept = _request(
                port, "GET", "/api/auth/status", headers={"Cookie": browsers[user_id]["Cookie"]}
            )
            assert kept[2]["access"]["principal"]["user_id"] == user_id

    anonymous = _request(port, "GET", "/api/settings/deployment")
    assert (anonymous[0], anonymous[2]["code"]) == (401, "authentication_required")
