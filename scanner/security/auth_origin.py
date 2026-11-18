"""Strict parsing and comparison of browser-facing authentication origins."""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_OWS = " \t"


class OriginParseError(ValueError):
    """An origin or referer does not meet the deliberately narrow HTTP grammar."""


@dataclass(frozen=True, slots=True)
class PublicOrigin:
    scheme: str
    host: str
    port: int

    @property
    def serialized(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        if (self.scheme, self.port) in {("http", 80), ("https", 443)}:
            return f"{self.scheme}://{host}"
        return f"{self.scheme}://{host}:{self.port}"


def parse_public_origin(value: str) -> PublicOrigin:
    """Parse the configured origin, permitting only one optional trailing slash."""

    origin = _parse_url(value, allow_trailing_slash=True, referer=False)
    return origin


def direct_request_origin(scheme: str, authority: str) -> PublicOrigin:
    """Validate the browser-facing authority without assuming an internal listener port."""

    origin = parse_origin_header(f"{scheme}://{authority}")
    try:
        address = ipaddress.ip_address(origin.host)
    except ValueError:
        return origin
    if address.is_unspecified or address.is_multicast:
        raise OriginParseError("direct access requires a usable IP")
    return origin


def is_local_bootstrap_host(host: str) -> bool:
    """Whether an implicit-local request host cannot be an attacker's rebound DNS name.

    Accepts IP literals, ``localhost``, ``*.localhost``, and this machine's hostname with
    or without ``.local``. ``host`` is a canonical ``PublicOrigin.host``.
    """

    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    host = host.casefold()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    machine = socket.gethostname().casefold().rstrip(".")
    if not machine:
        return False
    names = {machine, machine.split(".")[0]}
    return host in names | {f"{name}.local" for name in names}


def parse_origin_header(value: str) -> PublicOrigin:
    """Parse one serialized Origin header; an Origin must never contain a slash path."""

    return _parse_url(value, allow_trailing_slash=False, referer=False)


def parse_referer_header(value: str) -> PublicOrigin:
    """Parse an absolute Referer URL and retain only its exact origin tuple."""

    return _parse_url(value, allow_trailing_slash=True, referer=True)


def normalize_header_value(raw: bytes) -> str:
    """Decode and trim a source header without accepting embedded whitespace/control bytes."""

    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise OriginParseError("source header must be ASCII") from error
    value = value.strip(_OWS)
    if not value or any(ord(character) < 33 or ord(character) == 127 for character in value):
        raise OriginParseError("source header contains whitespace or controls")
    return value


def _parse_url(value: str, *, allow_trailing_slash: bool, referer: bool) -> PublicOrigin:
    """Validate an origin URL and return its normalized scheme, host, and port."""
    if not isinstance(value, str) or not value:
        raise OriginParseError("origin is required")
    if any(
        ord(character) <= 32 or ord(character) == 127 or character == "\\" for character in value
    ):
        raise OriginParseError("origin contains whitespace, controls, or a backslash")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise OriginParseError("origin has an invalid host or port") from error
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise OriginParseError("origin must use HTTP(S) and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise OriginParseError("origin must not contain userinfo")
    if parsed.fragment or (parsed.query and not referer):
        raise OriginParseError("origin must not contain a query or fragment")
    if not referer and parsed.path not in ({"", "/"} if allow_trailing_slash else {""}):
        raise OriginParseError("origin must not contain a path")
    if referer and parsed.path and not parsed.path.startswith("/"):
        raise OriginParseError("referer must be an absolute HTTP(S) URL")
    host = parsed.hostname
    if host is None or "%" in host:
        raise OriginParseError("origin has an invalid host")
    if ":" in host:
        # urlsplit only accepts IPv6 in brackets; retain ipaddress canonical spelling.
        try:
            canonical_host = str(ipaddress.IPv6Address(host))
        except ipaddress.AddressValueError as error:
            raise OriginParseError("origin has an invalid IPv6 host") from error
    else:
        try:
            canonical_host = str(ipaddress.ip_address(host))
        except ValueError:
            canonical_host = _canonical_dns_host(host)
    effective_port = port if port is not None else (443 if parsed.scheme == "https" else 80)
    if not 1 <= effective_port <= 65535:
        raise OriginParseError("origin port must be in 1..65535")
    return PublicOrigin(parsed.scheme.casefold(), canonical_host, effective_port)


def _canonical_dns_host(host: str) -> str:
    if not host.isascii() or host.endswith(".") or len(host) > 253:
        raise OriginParseError("origin DNS hostname must be ASCII without a trailing dot")
    canonical = host.casefold()
    if not all(_DNS_LABEL.fullmatch(label) for label in canonical.split(".")):
        raise OriginParseError("origin DNS hostname is invalid")
    return canonical
