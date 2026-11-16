"""Bounded HTTP exchanges between the Jellyfin user adapter and one server.

The adapter configures its own client, so no caller setting can follow a
redirect or run an authentication flow. A password or token therefore reaches
only the configured origin, once, through any proxy the environment names. Like
the catalog client, it honors the HTTPX environment settings: `HTTP_PROXY`,
`HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, and `SSL_CERT_DIR`.
Since HTTPX 0.24, honoring them reads no `.netrc` file.

A body is read as raw bytes and decoded here a bounded piece at a time, so
neither the wire bytes nor the decoded bytes pass the budget. Every network wait
is limited to the exchange's seconds, the deadline is checked after each read,
and a reply that completes after the deadline is a timeout. Every failure
becomes an `Unverified` reason instead of an exception.
"""

from __future__ import annotations

import json
import time
import zlib
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from .jellyfin_user_snapshot import InvalidPayload, Unverified, UnverifiedReason

_PIECE = 64 * 1024
# Window bits that let zlib detect a gzip or zlib header itself.
_GZIP_OR_ZLIB = 32 + zlib.MAX_WBITS
_COMPRESSED = {"": False, "identity": False, "gzip": True, "x-gzip": True, "deflate": True}


@dataclass(frozen=True)
class ResponseLimits:
    """Per-request budgets: body bytes by kind, and seconds for the whole exchange."""

    json_bytes: int = 8 * 1024 * 1024
    image_bytes: int = 5 * 1024 * 1024
    seconds: float = 30.0


@dataclass(frozen=True)
class Reply:
    """A fully read response. The body is empty for an allowed 404."""

    status_code: int
    content_type: str
    body: bytes

    def json(self) -> object:
        try:
            return json.loads(self.body)
        except (ValueError, RecursionError) as error:
            raise InvalidPayload("Response is not JSON") from error


def open_client(transport: httpx.BaseTransport | None, limits: ResponseLimits) -> httpx.Client:
    """Build the only client configuration the adapter sends through; tests supply `transport`."""
    return httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(limits.seconds),
        follow_redirects=False,
        trust_env=True,
        headers={"Accept-Encoding": "gzip, deflate"},
    )


def exchange(
    client: httpx.Client,
    request: httpx.Request,
    *,
    max_bytes: int,
    seconds: float,
    rejected: UnverifiedReason,
    allow_not_found: bool,
) -> Reply | Unverified:
    """Send `request` once and read at most `max_bytes` of a successful body within `seconds`."""
    deadline = time.monotonic() + seconds
    try:
        response = client.send(request, stream=True)
        try:
            outcome = _read(response, max_bytes, deadline, rejected, allow_not_found)
        finally:
            response.close()
    except httpx.TimeoutException:
        return Unverified(UnverifiedReason.TIMEOUT)
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError):
        return Unverified(UnverifiedReason.UNREACHABLE)
    if isinstance(outcome, Reply) and time.monotonic() > deadline:
        return Unverified(UnverifiedReason.TIMEOUT)
    return outcome


def _read(
    response: httpx.Response,
    max_bytes: int,
    deadline: float,
    rejected: UnverifiedReason,
    allow_not_found: bool,
) -> Reply | Unverified:
    status = response.status_code
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
    if allow_not_found and status == 404:
        return Reply(status, content_type, b"")
    if 300 <= status < 400:
        return Unverified(UnverifiedReason.REDIRECTED)
    if status in {401, 403}:
        return Unverified(rejected)
    if not 200 <= status < 300:
        return Unverified(UnverifiedReason.SERVICE_ERROR)
    encoding = response.headers.get("Content-Encoding", "").strip().lower()
    if encoding not in _COMPRESSED:
        return Unverified(UnverifiedReason.INVALID_RESPONSE)
    decoder = zlib.decompressobj(_GZIP_OR_ZLIB) if _COMPRESSED[encoding] else None
    received = 0
    body = bytearray()
    try:
        # `iter_raw` refuses a response its transport already read; the stream keeps the raw bytes.
        for raw in response.stream:
            received += len(raw)
            if received > max_bytes:
                return Unverified(UnverifiedReason.RESPONSE_TOO_LARGE)
            for piece in _decoded(decoder, raw):
                body += piece
                if len(body) > max_bytes:
                    return Unverified(UnverifiedReason.RESPONSE_TOO_LARGE)
            if time.monotonic() > deadline:
                return Unverified(UnverifiedReason.TIMEOUT)
    except zlib.error:
        return Unverified(UnverifiedReason.INVALID_RESPONSE)
    if decoder is not None and received and not decoder.eof:
        return Unverified(UnverifiedReason.INVALID_RESPONSE)
    return Reply(status, content_type, bytes(body))


def _decoded(decoder: zlib._Decompress | None, raw: bytes) -> Iterator[bytes]:
    """Yield `raw` decoded in pieces of at most `_PIECE` bytes, so no input expands at once."""
    if decoder is None:
        yield raw
        return
    while True:
        piece = decoder.decompress(raw, _PIECE)
        yield piece
        raw = decoder.unconsumed_tail
        if not raw and len(piece) < _PIECE:
            return
