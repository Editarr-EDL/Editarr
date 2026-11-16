"""The Jellyfin user adapter and the catalog client follow the same proxy and CA environment.

Local HTTP and HTTPS servers stand in for Jellyfin and for a proxy. These tests
are not live-server validation.
"""

from __future__ import annotations

import ipaddress
import json
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from scanner.integrations.jellyfin import JellyfinClient, JellyfinError, media_browser_authorization
from scanner.integrations.jellyfin_user_snapshot import Unverified, UnverifiedReason
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from tests.jellyfin_user_boundary import FIXTURE, free_port, local_server

SETTINGS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR")
SERVER_INFO = json.dumps(FIXTURE["servers"]["primary"]).encode()


def jellyfin(path: str) -> tuple[int, dict[str, str], bytes, float]:
    """Answer the adapter's server read and the catalog client's connection check."""
    body = SERVER_INFO if path.endswith("/System/Info/Public") else b"[]"
    return 200, {"Content-Type": "application/json"}, body, 0


def refuse(_path: str) -> tuple[int, dict[str, str], bytes, float]:
    return 502, {}, b"", 0


@pytest.fixture
def environment(monkeypatch, tmp_path):
    """Start from no proxy or certificate settings and a scratch home directory."""
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return monkeypatch


def both_clients(url: str) -> tuple[JellyfinError | None, object]:
    """Check the connection with the catalog client, then read the server with the adapter."""
    with JellyfinClient(url, "key") as catalog:
        try:
            catalog.test_connection()
            catalog_error = None
        except JellyfinError as error:
            catalog_error = error
    with JellyfinUserAdapter(url, "key") as adapter:
        return catalog_error, adapter.server()


def authority_for_localhost(directory: Path) -> tuple[Path, ssl.SSLContext]:
    """Write a fresh CA certificate and return it with a server context it signed for 127.0.0.1."""
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MU-08 test authority")])

    def certificate(subject: x509.Name, key: ec.EllipticCurvePrivateKey) -> x509.CertificateBuilder:
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(hours=1))
        )

    ca = (
        certificate(ca_name, ca_key)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )
    leaf = (
        certificate(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]), leaf_key)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
        )
        .sign(ca_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    ca_file = directory / "authority.pem"
    ca_file.write_bytes(ca.public_bytes(pem))
    chain = directory / "server.pem"
    chain.write_bytes(
        leaf.public_bytes(pem)
        + leaf_key.private_bytes(
            pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(chain)
    return ca_file, context


@pytest.mark.parametrize("variable", ["HTTP_PROXY", "http_proxy", "ALL_PROXY"])
def test_an_environment_proxy_carries_provider_requests(environment, variable):
    seen = []
    target = f"http://127.0.0.1:{free_port()}"

    with local_server(jellyfin, seen=seen) as proxy:
        environment.setenv(variable, proxy)
        catalog_error, server = both_clients(target)

    assert catalog_error is None
    assert not isinstance(server, Unverified)
    assert [path for path, _headers in seen] == [
        f"{target}/Library/VirtualFolders",
        f"{target}/System/Info/Public",
    ]


@pytest.mark.parametrize("bypass", ["127.0.0.1", "localhost,127.0.0.1", "*"])
def test_no_proxy_hosts_reach_the_server_directly(environment, bypass):
    seen = []

    with local_server(refuse, seen=seen) as proxy, local_server(jellyfin) as url:
        environment.setenv("HTTP_PROXY", proxy)
        environment.setenv("NO_PROXY", bypass)
        catalog_error, server = both_clients(url)

    assert catalog_error is None
    assert not isinstance(server, Unverified)
    assert seen == []


def test_ssl_cert_file_names_the_authorities_both_clients_trust(environment, tmp_path):
    authority, context = authority_for_localhost(tmp_path)
    environment.setenv("NO_PROXY", "127.0.0.1")

    with local_server(jellyfin, tls=context) as url:
        untrusted = both_clients(url)
        environment.setenv("SSL_CERT_FILE", str(authority))
        trusted = both_clients(url)

    assert isinstance(untrusted[0], JellyfinError)
    assert untrusted[1] == Unverified(UnverifiedReason.UNREACHABLE)
    assert trusted[0] is None
    assert not isinstance(trusted[1], Unverified)


def test_a_netrc_file_never_adds_credentials(environment, tmp_path):
    netrc = tmp_path / ".netrc"
    netrc.write_text(
        "machine 127.0.0.1 login netrc password netrc-secret\n"
        "default login netrc password netrc-secret\n"
    )
    netrc.chmod(0o600)
    environment.setenv("NETRC", str(netrc))
    environment.setenv("NO_PROXY", "127.0.0.1")
    seen = []

    with local_server(jellyfin, seen=seen) as url:
        with JellyfinUserAdapter(url, "key") as adapter:
            server = adapter.server()

    assert not isinstance(server, Unverified)
    assert [headers.get_all("Authorization") for _path, headers in seen] == [
        [media_browser_authorization(Token="key")]
    ]
