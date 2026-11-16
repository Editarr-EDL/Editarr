"""An in-process Jellyfin 12 stand-in for first-run setup tests.

It answers the routes setup calls with the shapes the prototype recorded against
`jellyfin/jellyfin@sha256:baba6304...`: sign-in, key list, key creation (204, no
body), key deletion, and logout. It proves nothing about a live server.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from tests.jellyfin_user_boundary import FIXTURE, IDS, PRIMARY, user_dto

ADMIN, ADMIN_PASSWORD = "Avery", "admin-password-1"
MEMBER, MEMBER_PASSWORD = "Blake", "member-password-1"
SERVER_URL = "http://jellyfin.test"


@dataclass
class FakeJellyfin:
    """Users by name with (password, administrator); keys and sessions as Jellyfin holds them."""

    users: dict[str, tuple[str, bool]] = field(
        default_factory=lambda: {
            ADMIN: (ADMIN_PASSWORD, True),
            MEMBER: (MEMBER_PASSWORD, False),
        }
    )
    keys: list[dict] = field(default_factory=list)
    sessions: dict[str, str] = field(default_factory=dict)
    devices: dict[str, tuple[str | None, str]] = field(default_factory=dict)
    logged_out: list[str] = field(default_factory=list)
    fail_create: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def editarr_keys(self) -> list[str]:
        return [key["AccessToken"] for key in self.keys if key["AppName"] == "Editarr"]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._answer)

    def _field(self, request: httpx.Request, field: str) -> str | None:
        header = request.headers.get("Authorization", "")
        if not header.startswith("MediaBrowser "):
            return None
        for part in header.removeprefix("MediaBrowser ").split(", "):
            name, _, value = part.partition("=")
            if name == field:
                return value.strip('"')
        return None

    def _token(self, request: httpx.Request) -> str | None:
        return self._field(request, "Token")

    def _answer(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        with self.lock:
            if path == "/System/Info/Public":
                return httpx.Response(200, json=FIXTURE["servers"]["primary"])
            if path == "/Users/AuthenticateByName" and method == "POST":
                if not request.headers.get("Authorization", "").startswith("MediaBrowser "):
                    return httpx.Response(400)
                body = httpx.Response(200, content=request.content).json()
                stored = self.users.get(body["Username"])
                if stored is None or stored[0] != body["Pw"]:
                    return httpx.Response(401)
                user = user_dto(body["Username"])
                user["Policy"] = {**user["Policy"], "IsAdministrator": stored[1]}
                token = secrets.token_hex(16)
                # Jellyfin keeps one session per user and device: a new sign-in ends the old one.
                device = (self._field(request, "DeviceId"), body["Username"])
                self.sessions = {
                    held: holder
                    for held, holder in self.sessions.items()
                    if self.devices.get(held) != device
                }
                self.devices[token] = device
                self.sessions[token] = body["Username"]
                return httpx.Response(
                    200, json={"User": user, "AccessToken": token, "ServerId": PRIMARY}
                )
            name = self.sessions.get(self._token(request) or "")
            if name is None:
                return httpx.Response(401)
            if path == "/Sessions/Logout" and method == "POST":
                self.logged_out.append(name)
                self.sessions.pop(self._token(request) or "", None)
                return httpx.Response(204)
            if not self.users[name][1]:
                return httpx.Response(403)
            if path == "/Auth/Keys" and method == "GET":
                return httpx.Response(200, json={"Items": self.keys, "TotalRecordCount": 0})
            if path == "/Auth/Keys" and method == "POST":
                if self.fail_create:
                    return httpx.Response(500)
                app = request.url.params["app"]
                self.keys.append({"AccessToken": secrets.token_hex(16), "AppName": app})
                return httpx.Response(204)
            if path.startswith("/Auth/Keys/") and method == "DELETE":
                key = path.removeprefix("/Auth/Keys/")
                self.keys = [item for item in self.keys if item["AccessToken"] != key]
                return httpx.Response(204)
        return httpx.Response(404)


def setup_body(username: str = ADMIN, password: str = ADMIN_PASSWORD, **extra) -> dict:
    """A setup request for the fake server."""
    return {"server_url": SERVER_URL, "username": username, "password": password, **extra}


def install(monkeypatch, fake: FakeJellyfin | None = None) -> FakeJellyfin:
    """Route setup to `fake` (a new one by default) and return it."""
    from scanner.http import jellyfin_setup_api

    fake = fake or FakeJellyfin()
    monkeypatch.setattr(jellyfin_setup_api, "setup_transport", fake.transport())
    monkeypatch.setattr(
        jellyfin_setup_api, "setup_rate_limiter", type(jellyfin_setup_api.setup_rate_limiter)()
    )
    return fake


ADMIN_ID = IDS[ADMIN]


@contextmanager
def serve(fake: FakeJellyfin | None = None) -> Iterator[tuple[FakeJellyfin, str]]:
    """Serve `fake` on loopback HTTP for an Editarr process that cannot take a transport."""
    fake = fake or FakeJellyfin()

    class Handler(BaseHTTPRequestHandler):
        def _relay(self):
            length = int(self.headers.get("Content-Length") or 0)
            request = httpx.Request(
                self.command,
                f"http://{self.headers['Host']}{self.path}",
                headers=dict(self.headers),
                content=self.rfile.read(length),
            )
            reply = fake._answer(request)
            self.send_response(reply.status_code)
            body = reply.content
            if body:
                self.send_header(
                    "Content-Type", reply.headers.get("Content-Type", "application/json")
                )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_DELETE = _relay

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
