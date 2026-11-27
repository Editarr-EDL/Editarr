"""Local review server: static evaluation_data (with Range) plus the corrections API."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .corrections import (
    CorrectionError,
    correction_count,
    effective_expected,
    load_corrections,
    save_correction,
    validate_request,
)
from .paths import EXCLUDED_DIRS, Layout, excluded_real_paths
from .server_state import InventoryIndex, Summarizer
from .summary import summarize

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 16 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


class _Slice:
    """File wrapper that yields at most `length` bytes (for 206 responses)."""

    def __init__(self, handle, length: int) -> None:
        self._handle = handle
        self._remaining = length

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        size = self._remaining if size < 0 else min(size, self._remaining)
        data = self._handle.read(size)
        self._remaining -= len(data)
        return data

    def close(self) -> None:
        self._handle.close()


class ReviewHandler(SimpleHTTPRequestHandler):
    """Static files from the data root; `/_api/*` for status and corrections."""

    server: ReviewServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        log.debug("%s " + format, self.address_string(), *args)

    # --- static -------------------------------------------------------------------------

    def end_headers(self) -> None:
        if not self.path.startswith("/_api/"):
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def _excluded(self, fs_path: str) -> bool:
        """Block excluded folders by request path and by resolved target (symlinks)."""
        relative = os.path.relpath(fs_path, self.directory).replace(os.sep, "/").casefold()
        if any(relative == d or relative.startswith(d + "/") for d in EXCLUDED_DIRS):
            return True
        resolved = os.path.realpath(fs_path).casefold()
        return any(
            resolved == blocked or resolved.startswith(blocked + os.sep)
            for blocked in self.server.blocked_real_paths
        )

    def _host_allowed(self) -> bool:
        """DNS-rebinding guard for every request: Host must name this local server."""
        if self.headers.get("Host", "").lower() in self.server.allowed_hosts:
            return True
        self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
        return False

    def do_HEAD(self) -> None:
        if self._host_allowed():
            super().do_HEAD()

    def send_head(self):
        fs_path = self.translate_path(self.path)
        if self._excluded(fs_path):
            self.send_error(HTTPStatus.NOT_FOUND)
            return None
        match = _RANGE.match(self.headers.get("Range", "").strip())
        if not match or not os.path.isfile(fs_path):
            return super().send_head()
        size = os.path.getsize(fs_path)
        first, last = match.groups()
        if first:
            start, end = int(first), int(last) if last else size - 1
        elif last:
            start, end = max(0, size - int(last)), size - 1
        else:
            return super().send_head()
        end = min(end, size - 1)
        if start > end or start >= size:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        handle = open(fs_path, "rb")  # noqa: SIM115 - closed by copyfile's caller
        handle.seek(start)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(fs_path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        return _Slice(handle, end - start + 1)

    # --- API ----------------------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._host_allowed():
            return
        route = urlsplit(self.path).path
        if not route.startswith("/_api/"):
            super().do_GET()
            return
        if not self._same_origin():
            return
        if route == "/_api/status":
            self._json(HTTPStatus.OK, self.server.status())
        elif route == "/_api/corrections":
            self._json(HTTPStatus.OK, load_corrections(self.server.layout))
        else:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown endpoint"})

    def do_POST(self) -> None:
        if not self._host_allowed():
            return
        if urlsplit(self.path).path != "/_api/corrections":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown endpoint"})
            return
        if not self._same_origin():
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body exceeds 16 KB")
            return
        try:
            body = json.loads(self.rfile.read(length) or b"null")
            expected = self.server.inventory.expected()
            item_id, key, value, note = validate_request(body, expected)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "body is not valid JSON")
            return
        except CorrectionError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        item_map = save_correction(self.server.layout, item_id, key, value, note)
        self.server.summarizer.request()
        self._json(HTTPStatus.OK, {
            "ok": True,
            "item_id": item_id,
            "corrections": item_map,
            "effective_expected": effective_expected(expected[item_id], item_map),
            "summary_pending": True,
        })

    def _same_origin(self) -> bool:
        """CSRF guard for the API: any Origin must be this server and not cross-site."""
        allowed = self.server.allowed_hosts
        origin = self.headers.get("Origin")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if (
            (origin is not None and urlsplit(origin).netloc.lower() not in allowed)
            or (origin is not None and urlsplit(origin).scheme != "http")
            or fetch_site not in (None, "same-origin", "none")
        ):
            self._error(HTTPStatus.FORBIDDEN, "cross-origin request rejected")
            return False
        return True

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, layout: Layout, port: int = 8765, debounce_s: float = 2.0) -> None:
        handler = partial(ReviewHandler, directory=str(layout.data_root))
        super().__init__(("127.0.0.1", port), handler)
        self.layout = layout
        bound = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{bound}", f"localhost:{bound}"}
        self.blocked_real_paths = excluded_real_paths(layout.data_root)
        self.inventory = InventoryIndex(layout)
        self.summarizer = Summarizer(self._refresh_summary, debounce_s)

    def _refresh_summary(self) -> None:
        """Summarize, then re-queue if corrections changed after the summary read them."""
        payload = summarize(self.layout)
        used = payload["corrections"]["updated_at"]
        if load_corrections(self.layout).get("updated_at") != used:
            self.summarizer.request()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def status(self) -> dict[str, Any]:
        summary: Path = self.layout.summary
        updated = None
        if summary.is_file():
            updated = datetime.fromtimestamp(summary.stat().st_mtime, timezone.utc).isoformat(
                timespec="seconds"
            )
        status = {
            "writable": True,
            "summary_updated_at": updated,
            "summarizing": self.summarizer.busy,
            "corrections": correction_count(load_corrections(self.layout)),
        }
        if self.summarizer.last_error:
            status["summary_error"] = self.summarizer.last_error
        return status


def serve(layout: Layout, port: int = 8765) -> None:
    """Serve until interrupted."""
    server = ReviewServer(layout, port)
    log.info("Serving %s at http://127.0.0.1:%d/_results/index.html", layout.data_root, server.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
