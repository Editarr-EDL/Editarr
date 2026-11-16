"""`normalize_server_url` returns a usable Jellyfin base URL or raises `JellyfinError`."""

from __future__ import annotations

import itertools
import random

import httpx
import pytest

from scanner.integrations.jellyfin import JellyfinError
from scanner.integrations.jellyfin_server_url import normalize_server_url

LONG_LABEL = "a" * 64

MALFORMED = [
    "",
    "   ",
    "jellyfin.local",
    "ftp://jellyfin.local",
    "javascript:alert(1)",
    "http://",
    "http:///path",
    "https://:8096",
    "http://@",
    "http://[::1",
    "http://::1]",
    "http://[zz]/",
    "http://[::1]x/",
    "http://host:99999",
    "http://host:65536",
    "http://host:0",
    "http://host:-1",
    "http://host:abc",
    "http://host:8096:8096",
    f"http://{LONG_LABEL}.test",
    f"http://{LONG_LABEL}",
    "http://" + "a." * 127 + "test",
    "http://a..b",
    "http://.test",
    "http://exa mple",
    "http://host\x00/",
    "http://user:pass@host",
    "http://host/?q=1",
    "http://host/#frag",
    "http://xn--zz",
    "http://XN--zz",
    "http://xn--a.example",
    "http://xn--ls8h.la",
    "http://jellyfin.xn--zz",
    "http://jellyfin.xn--ls8h:8096",
    "http://xn--.test",
    "http://exämple.xn--zz",
    "http://℡.test",
]

VALID = [
    ("http://jellyfin.local:8096", "http://jellyfin.local:8096"),
    (" https://jellyfin.example.com/jf/ ", "https://jellyfin.example.com/jf"),
    ("http://127.0.0.1:8096", "http://127.0.0.1:8096"),
    ("http://[::1]:8096", "http://[::1]:8096"),
    ("http://192.168.1.20", "http://192.168.1.20"),
    ("http://jelly\tfin/", "http://jellyfin"),
    ("http://jellyfin_media:8096", "http://jellyfin_media:8096"),
    ("http://exämple.test", "http://exämple.test"),
    (f"http://{'a' * 63}.test", f"http://{'a' * 63}.test"),
    ("http://xn--fiqs8s.icom.museum", "http://xn--fiqs8s.icom.museum"),
    ("http://jellyfin.xn--fiqs8s", "http://jellyfin.xn--fiqs8s"),
]


@pytest.mark.parametrize("value", MALFORMED)
def test_a_malformed_address_is_refused(value):
    with pytest.raises(JellyfinError):
        normalize_server_url(value)


@pytest.mark.parametrize(("value", "normalized"), VALID)
def test_a_usable_address_is_normalized(value, normalized):
    assert normalize_server_url(value) == normalized


def _usable(url: str) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        request = client.build_request("GET", f"{url}/System/Info/Public")
        client.send(request)
    parsed = request.url
    assert parsed.scheme in {"http", "https"} and parsed.host
    assert parsed.port is None or 0 < parsed.port < 65536
    parsed.raw_host.decode("ascii")
    assert not any(character.isspace() or ord(character) < 32 for character in parsed.host)


@pytest.mark.parametrize(
    ("scheme", "host", "port"),
    list(
        itertools.product(
            ["http://", "https://"],
            [
                "",
                "[::1",
                "::1]",
                LONG_LABEL,
                "a..b",
                "ex ample",
                "[::1]",
                "jellyfin",
                "10.0.0.2",
                "xn--zz",
                "xn--ls8h.la",
                "a.xn--a",
                "xn--fiqs8s",
            ],
            ["", ":", ":0", ":8096", ":99999", ":x"],
        )
    ),
)
def test_every_scheme_host_and_port_combination_is_usable_or_refused(scheme, host, port):
    try:
        normalized = normalize_server_url(scheme + host + port)
    except JellyfinError:
        return
    _usable(normalized)


def test_random_addresses_are_usable_or_refused():
    alphabet = "ab1.:-_/[]@?#% \t\x00é" + LONG_LABEL[:8]
    pieces = [*alphabet, "xn--", "XN--", "xn--ls8h", "xn--fiqs8s", "💩", "ß"]
    rng = random.Random(20261103)
    for _ in range(3000):
        tail = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 24)))
        value = rng.choice(["http://", "https://", "http:", ""]) + tail
        try:
            normalized = normalize_server_url(value)
        except JellyfinError:
            continue
        _usable(normalized)
