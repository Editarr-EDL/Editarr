"""A controlled local HTTP stand-in for Jellyfin, served from the jellyfin-users-v1 fixture.

It answers the routes the user adapter reads with recorded synthetic shapes. It is
an external-boundary substitute and proves nothing about a live Jellyfin server.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import socket
import ssl
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from scanner.formats.export_layout import JellyfinUserId
from scanner.security.access.identity import JellyfinServerId, ProviderIdentity

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "multi-user" / "jellyfin-users-v1" / "manifest.json"
)
FIXTURE = json.loads(FIXTURE_PATH.read_text())
PORTS = range(8640, 8660)
AVATAR_BYTES = b"\x89PNG\r\n\x1a\nsynthetic-avatar"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
PRIMARY = JellyfinServerId(FIXTURE["servers"]["primary"]["Id"])
REPLACEMENT = JellyfinServerId(FIXTURE["servers"]["replacement"]["Id"])
IDS = {user["Name"]: user["Id"] for user in FIXTURE["states"]["baseline"]["users"]}
FEATURE, FAMILY_FILM, LATE_SHOW, MATURE = sorted(FIXTURE["items"])
_FIELD = re.compile(r'(\w+)="([^"]*)"')


def identity(name: str, server: JellyfinServerId = PRIMARY) -> ProviderIdentity:
    """The provider identity of a baseline user, named only to find the fixture GUID."""
    return ProviderIdentity(server_id=server, user_id=JellyfinUserId(IDS[name]))


def user_dto(name: str, server: str = PRIMARY) -> dict:
    """A baseline `UserDto` for `name` as served by `server`."""
    return {**FIXTURE["userDtoTemplate"], "Id": IDS[name], "Name": name, "ServerId": server}


def sign_in_result(user: dict, server: str = PRIMARY) -> dict:
    """An `AuthenticationResult` for `user` with a fresh synthetic token."""
    return {"User": user, "AccessToken": secrets.token_hex(16), "ServerId": server}


def scripted_transport(
    answer: Callable[[httpx.Request], httpx.Response],
    servers: tuple[str, ...] = ("primary",),
) -> httpx.MockTransport:
    """A transport whose `/System/Info/Public` reads return `servers` in turn, then the last.

    Every other request gets `answer(request)`.
    """
    queue = list(servers)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/System/Info/Public":
            return httpx.Response(
                200, json=FIXTURE["servers"][queue.pop(0) if queue[1:] else queue[0]]
            )
        return answer(request)

    return httpx.MockTransport(handler)


def free_port() -> int:
    """Return a port in the assigned range with nothing listening on it."""
    for port in PORTS:
        with socket.socket() as probe:
            # Match the server's SO_REUSEADDR so closed connections in TIME_WAIT do not hold ports.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free port in 8640-8659")


@contextmanager
def local_server(
    answer: Callable[[str], tuple[int, dict[str, str], bytes, float]],
    *,
    seen: list[tuple[str, Message]] | None = None,
    tls: ssl.SSLContext | None = None,
) -> Iterator[str]:
    """Serve `answer(path) -> (status, headers, body, delay)` on 127.0.0.1 until the block ends.

    Each request's path and headers are appended to `seen`. `tls` serves HTTPS.
    """
    released = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def respond(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if seen is not None:
                seen.append((self.path, self.headers))
            status, headers, body, delay = answer(self.path)
            released.wait(delay)
            try:
                self.send_response(status)
                for name, value in {**headers, "Content-Length": str(len(body))}.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = do_POST = respond

    server = ThreadingHTTPServer(("127.0.0.1", free_port()), Handler)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"{'https' if tls else 'http'}://127.0.0.1:{server.server_address[1]}"
    finally:
        released.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def _merge(base: dict, patch: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


class JellyfinBoundary:
    """Serve one fixture state, optionally under a named fault, on 127.0.0.1."""

    def __init__(self, api_key: str, passwords: dict[str, str]):
        self.fixture = FIXTURE
        self.api_key = api_key
        self.passwords = passwords
        self.state = "baseline"
        self.fault: str | None = None
        self.requests: list[str] = []
        self.issued_tokens: list[str] = []
        self.open_tokens: set[str] = set()
        self.quick_connect_enabled = True
        self.quick_connect: dict[str, dict] = {}
        """Requests by secret: `code`, and `user` once approved. Like Jellyfin 12, sign-in
        does not spend a secret."""
        self._release = threading.Event()
        self._server = ThreadingHTTPServer(("127.0.0.1", free_port()), self._handler())
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> JellyfinBoundary:
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def server_info(self) -> dict:
        return self.fixture["servers"][self.fixture["states"][self.state]["server"]]

    def users(self) -> list[dict]:
        state = self.fixture["states"][self.state]
        server_id = self.server_info()["Id"]
        return [
            _merge(self.fixture["userDtoTemplate"], {**user, "ServerId": server_id})
            for user in state["users"]
        ]

    def _user(self, user_id: str) -> dict | None:
        wanted = user_id.replace("-", "").lower()
        return next((user for user in self.users() if user["Id"] == wanted), None)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        boundary = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_GET(self) -> None:
                boundary._dispatch(self, "GET")

            def do_POST(self) -> None:
                boundary._dispatch(self, "POST")

        return Handler

    def _dispatch(self, request: BaseHTTPRequestHandler, method: str) -> None:
        url = urlsplit(request.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.requests.append(f"{method} {url.path} {sorted(query)}")
        body = request.rfile.read(int(request.headers.get("Content-Length") or 0))
        fault = self.fixture["faults"].get(self.fault or "")
        if fault and self._fault_applies(fault, method, url.path):
            if fault["kind"] == "delay":
                if not self._release.wait(5):
                    _send(request, 200, "application/json", b"{}")
                return None
            return _send(request, fault["status"], fault["contentType"], fault["body"].encode())
        fields = {
            key: unquote(value)
            for key, value in _FIELD.findall(
                request.headers.get("Authorization", "").removeprefix("MediaBrowser ")
            )
        }
        if method == "GET" and url.path == "/System/Info/Public":
            return _json(request, 200, self.server_info())
        if method == "POST" and url.path == "/Users/AuthenticateByName":
            return self._sign_in(request, fields, body)
        if url.path.startswith(("/QuickConnect/", "/Users/AuthenticateWithQuickConnect")):
            return self._quick_connect(request, method, url.path, query, fields, body)
        if method == "POST" and url.path == "/Sessions/Logout":
            if fields.get("Token") not in self.open_tokens:
                return _send(request, 401, "text/plain", b"")
            self.open_tokens.discard(fields["Token"])
            return _send(request, 204, "text/plain", b"")
        if fields.get("Token") != self.api_key:
            return _send(request, 401, "text/plain", b"")
        if method == "GET" and url.path == "/Users":
            return _json(request, 200, self.users())
        if method == "GET" and url.path.startswith("/Users/"):
            user = self._user(url.path.removeprefix("/Users/"))
            if user is None:
                return _send(request, 404, "text/plain", b"User not found")
            return _json(request, 200, user)
        if method == "GET" and url.path == "/Items":
            return self._items(request, query)
        if method == "GET" and url.path.startswith("/Items/"):
            return self._user_item(request, url.path.removeprefix("/Items/"), query)
        if method == "GET" and url.path == "/UserImage":
            user = self._user(query.get("userId", ""))
            if user is None or not user.get("PrimaryImageTag"):
                return _send(request, 404, "text/plain", b"")
            return _send(request, 200, "image/png", AVATAR_BYTES)
        return _send(request, 404, "text/plain", b"")

    def approve(self, code: str, name: str) -> None:
        """Approve the pending request showing `code` as baseline user `name`."""
        request = next(entry for entry in self.quick_connect.values() if entry["code"] == code)
        request["user"] = IDS[name]

    def expire(self, code: str) -> None:
        """Forget the request showing `code`, as Jellyfin does ten minutes after it starts."""
        self.quick_connect = {
            secret: entry for secret, entry in self.quick_connect.items() if entry["code"] != code
        }

    def _quick_connect(
        self,
        request: BaseHTTPRequestHandler,
        method: str,
        path: str,
        query: dict[str, str],
        fields: dict,
        body: bytes,
    ) -> None:
        if path == "/QuickConnect/Enabled":
            return _json(request, 200, self.quick_connect_enabled)
        if (
            path == "/QuickConnect/Initiate"
            and not {
                "Client",
                "Device",
                "DeviceId",
                "Version",
            }
            <= fields.keys()
        ):
            return _send(request, 400, "text/plain", b"Error processing request.")
        if not self.quick_connect_enabled:
            return _json(request, 401, "Quick connect is disabled")
        if method == "POST" and path == "/QuickConnect/Initiate":
            secret = secrets.token_hex(32).upper()
            self.quick_connect[secret] = {"code": f"{secrets.randbelow(10**6):06d}", "user": None}
            return _json(request, 200, self._quick_connect_result(secret))
        if method == "GET" and path == "/QuickConnect/Connect":
            if query.get("secret") not in self.quick_connect:
                return _json(request, 404, "Unknown secret")
            return _json(request, 200, self._quick_connect_result(query["secret"]))
        if method == "POST" and path == "/Users/AuthenticateWithQuickConnect":
            entry = self.quick_connect.get(json.loads(body).get("Secret"), {})
            user = self._user(entry.get("user") or "")
            if user is None:
                return _send(request, 404, "text/plain", b"Error processing request.")
            token = secrets.token_hex(16)
            self.issued_tokens.append(token)
            self.open_tokens.add(token)
            return _json(
                request,
                200,
                {"User": user, "AccessToken": token, "ServerId": self.server_info()["Id"]},
            )
        return _send(request, 404, "text/plain", b"")

    def _quick_connect_result(self, secret: str) -> dict:
        entry = self.quick_connect[secret]
        return {"Authenticated": entry["user"] is not None, "Secret": secret, "Code": entry["code"]}

    def _fault_applies(self, fault: dict, method: str, path: str) -> bool:
        prefixes = fault.get("pathPrefixes")
        methods = fault.get("methods")
        return (not prefixes or any(path.startswith(prefix) for prefix in prefixes)) and (
            not methods or method in methods
        )

    def _sign_in(self, request: BaseHTTPRequestHandler, fields: dict, body: bytes) -> None:
        if not {"Client", "Device", "DeviceId", "Version"} <= fields.keys():
            return _send(request, 400, "text/plain", b"")
        credentials = json.loads(body)
        user = next(
            (
                user
                for user in self.users()
                if user["Name"].casefold() == str(credentials.get("Username")).casefold()
            ),
            None,
        )
        if user is None or self.passwords.get(user["Id"]) != credentials.get("Pw"):
            return _send(request, 401, "text/plain", b"Invalid username or password entered.")
        if user["Policy"]["IsDisabled"]:
            return _send(request, 403, "text/plain", b"")
        token = secrets.token_hex(16)
        self.issued_tokens.append(token)
        self.open_tokens.add(token)
        return _json(
            request,
            200,
            {
                "User": user,
                "SessionInfo": {"UserId": user["Id"], "Client": fields["Client"]},
                "AccessToken": token,
                "ServerId": self.server_info()["Id"],
            },
        )

    def _items(self, request: BaseHTTPRequestHandler, query: dict[str, str]) -> None:
        state = self.fixture["states"][self.state]
        visible = set(self.fixture["items"])
        if "userId" in query:
            user = self._user(query["userId"])
            if user is None:
                return _send(request, 404, "text/plain", b"")
            # Jellyfin 12 skips library access for this query; idsQueryItems records it.
            observed = state.get("idsQueryItems", {}).get(user["Id"])
            visible = set(observed or state["visibleItems"][user["Id"]])
        wanted = [value for value in query.get("Ids", "").split(",") if value in visible]
        items = [self._item(item_id) for item_id in wanted]
        return _json(
            request, 200, {"Items": items, "TotalRecordCount": len(items), "StartIndex": 0}
        )

    def _user_item(
        self, request: BaseHTTPRequestHandler, item_id: str, query: dict[str, str]
    ) -> None:
        if "userId" not in query:
            return _send(request, 400, "text/plain", b"")
        user = self._user(query["userId"])
        visible = self.fixture["states"][self.state]["visibleItems"].get(
            user["Id"] if user else "", []
        )
        if item_id not in visible:
            return _send(request, 404, "text/plain", b"")
        return _json(request, 200, self._item(item_id))

    def _item(self, item_id: str) -> dict:
        return {"Name": self.fixture["items"][item_id]["Name"], "Id": item_id, "Type": "Movie"}


def _send(request: BaseHTTPRequestHandler, status: int, content_type: str, body: bytes) -> None:
    request.send_response(status)
    request.send_header("Content-Type", content_type)
    request.send_header("Content-Length", str(len(body)))
    request.end_headers()
    request.wfile.write(body)


def _json(request: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    _send(request, status, "application/json; charset=utf-8", json.dumps(payload).encode())
