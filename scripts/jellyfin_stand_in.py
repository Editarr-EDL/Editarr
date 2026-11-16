"""A standard-library loopback Jellyfin stand-in for installed-artifact verification.

The installed-release verifier runs under a bare ``python3`` in CI, so it cannot
import ``tests.jellyfin_setup_fake`` (httpx, ``scanner``). This serves the same
recorded fixture and the same answers for the routes setup and sign-in call:
sign-in, key list, key creation (204, no body), key deletion, and logout. The
minted key also reads the administrator's user record and, when served a media
file, the one movie and library that creating a scan looks up. It proves nothing
about a live server.
"""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ADMIN, ADMIN_PASSWORD = "Avery", "admin-password-1"
_FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "multi-user"
        / "jellyfin-users-v1"
        / "manifest.json"
    ).read_text()
)
_SERVER = _FIXTURE["servers"]["primary"]
_ADMIN_ID = next(
    user["Id"] for user in _FIXTURE["states"]["baseline"]["users"] if user["Name"] == ADMIN
)
MOVIE_ID, _LIBRARY_ID = "5eed0000000000000000000000000001", "5eed0000000000000000000000000002"


def _administrator() -> dict:
    user = {**_FIXTURE["userDtoTemplate"], "Id": _ADMIN_ID, "Name": ADMIN}
    user["ServerId"] = _SERVER["Id"]
    user["Policy"] = {**user["Policy"], "IsAdministrator": True}
    return user


def _token(header: str) -> str | None:
    if not header.startswith("MediaBrowser "):
        return None
    for part in header.removeprefix("MediaBrowser ").split(", "):
        name, _, value = part.partition("=")
        if name == "Token":
            return value.strip('"')
    return None


def _movie(media: Path) -> dict:
    """Jellyfin 12's `/Items?Ids=` record for a one-second movie; it omits `LibraryId`."""
    source = {"Id": MOVIE_ID, "Path": str(media), "Name": media.stem, "Container": "mp4"}
    return {
        "Id": MOVIE_ID,
        "Name": media.stem,
        "Type": "Movie",
        "Path": str(media),
        "RunTimeTicks": 10_000_000,
        "ServerId": _SERVER["Id"],
        "MediaSources": [source],
    }


class JellyfinStandIn:
    """One administrator, its sessions, the keys it minted, and an optional movie."""

    def __init__(self, media: Path | None = None) -> None:
        self.media = media
        self.sessions: set[str] = set()
        self.keys: list[dict] = []
        self.lock = threading.Lock()

    def answer(
        self, method: str, target: str, authorization: str, body: bytes
    ) -> tuple[int, object | None]:
        """Return the status and JSON body Jellyfin 12 gives this request."""
        url = urlsplit(target)
        path = url.path
        with self.lock:
            if path == "/System/Info/Public":
                return 200, _SERVER
            if path == "/Users/AuthenticateByName" and method == "POST":
                if not authorization.startswith("MediaBrowser "):
                    return 400, None
                credentials = json.loads(body or b"{}")
                if (credentials.get("Username"), credentials.get("Pw")) != (
                    ADMIN,
                    ADMIN_PASSWORD,
                ):
                    return 401, None
                token = secrets.token_hex(16)
                self.sessions.add(token)
                user = _administrator()
                return 200, {"User": user, "AccessToken": token, "ServerId": _SERVER["Id"]}
            token = _token(authorization) or ""
            if token in {key["AccessToken"] for key in self.keys}:
                if path == "/Users":
                    return 200, [_administrator()]
                if path == f"/Users/{_ADMIN_ID}":
                    return 200, _administrator()
                if path == "/Library/VirtualFolders" and self.media:
                    library = {"Name": "Movies", "ItemId": _LIBRARY_ID, "CollectionType": "movies"}
                    return 200, [{**library, "Locations": [str(self.media.parent)]}]
                if path == "/Items" and self.media:
                    ids = parse_qs(url.query).get("Ids", [""])[0].split(",")
                    items = [_movie(self.media)] if MOVIE_ID in ids else []
                    return 200, {"Items": items, "TotalRecordCount": len(items)}
            if token not in self.sessions:
                return 401, None
            if path == "/Sessions/Logout" and method == "POST":
                self.sessions.discard(token)
                return 204, None
            if path == "/Auth/Keys" and method == "GET":
                return 200, {"Items": self.keys, "TotalRecordCount": 0}
            if path == "/Auth/Keys" and method == "POST":
                app = parse_qs(url.query).get("app", [""])[0]
                self.keys.append({"AccessToken": secrets.token_hex(16), "AppName": app})
                return 204, None
            if path.startswith("/Auth/Keys/") and method == "DELETE":
                key = path.removeprefix("/Auth/Keys/")
                self.keys = [item for item in self.keys if item["AccessToken"] != key]
                return 204, None
        return 404, None


@contextmanager
def serve(media: Path | None = None) -> Iterator[str]:
    """Serve a fresh stand-in on loopback HTTP and yield its URL.

    With `media`, the stand-in's movie `MOVIE_ID` points at that file; it need not exist yet.
    """
    stand_in = JellyfinStandIn(media)

    class Handler(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            status, payload = stand_in.answer(
                self.command,
                self.path,
                self.headers.get("Authorization", ""),
                self.rfile.read(length),
            )
            body = b"" if payload is None else json.dumps(payload).encode()
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_DELETE = _reply  # noqa: N815 - stdlib handler contract

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
