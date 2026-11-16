"""Validate the Jellyfin server address an administrator types."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

import httpx
import idna

from .jellyfin_errors import JellyfinError

_BAD_URL = "Enter a valid http or https Jellyfin server address"


def normalize_server_url(value: str) -> str:
    """Validate a Jellyfin base URL without permitting credentials or fragments.

    The host must be an IP literal or a name that the `idna` package, which
    httpx uses, can encode and whose `xn--` labels it can decode. Any port must
    be 1-65535 and httpx must build a request for the result, so an address
    that passes never fails while a request is being built or resolved.
    """
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        host, port = parsed.hostname, parsed.port
    except ValueError as error:
        raise JellyfinError(_BAD_URL) from error
    if parsed.scheme not in {"http", "https"} or not host or port == 0 or not _valid_host(host):
        raise JellyfinError(_BAD_URL)
    if parsed.username is not None or parsed.password or parsed.query or parsed.fragment:
        raise JellyfinError(
            "Jellyfin server address must not include credentials, query, or fragment"
        )
    normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    try:
        httpx.Request("GET", normalized)
    except (httpx.InvalidURL, UnicodeError) as error:
        raise JellyfinError(_BAD_URL) from error
    return normalized


def _valid_host(host: str) -> bool:
    try:
        ip_address(host)
        return True
    except ValueError:
        pass
    if any(character.isspace() or not character.isprintable() for character in host):
        return False
    try:
        ascii_host = host if host.isascii() else idna.encode(host.lower()).decode("ascii")
        for label in ascii_host.split("."):
            if label[:4].lower() == "xn--":
                idna.decode(label)
        return len(ascii_host.encode("idna")) <= 253
    except UnicodeError:
        return False
