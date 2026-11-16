import base64
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scanner.security.community_signatures import SignatureProfileError, sign_request

VECTORS = Path(__file__).resolve().parents[1] / (
    "community-service/test/fixtures/request-signatures-v1.json"
)


def test_empty_request_signs_exact_digest_and_query():
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    signed = sign_request(
        private_key=private_key,
        key_id="fixture-key",
        method="GET",
        url="https://community.example/v1/lookup?title=a%2Fb&title=c+d",
        body=b"",
        created=1_700_000_000,
        expires=1_700_000_060,
        nonce="AQIDBAUGBwgJCgsMDQ4PEA",
    )
    digest = base64.b64encode(hashlib.sha256(b"").digest()).decode("ascii")
    assert signed["content-digest"] == f"sha-256=:{digest}:"
    assert '"@query": ?title=a%2Fb&title=c+d' in signed["signature-base"].decode("ascii")


def test_mutation_covers_operation_and_transmitted_gzip_bytes():
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    signed = sign_request(
        private_key=private_key,
        key_id="fixture-key",
        method="POST",
        url="https://community.example/v1/contributions",
        body=b"\x1f\x8b\x08\x00",
        content_type="application/json",
        content_encoding="gzip",
        operation_id="operation-001",
        created=1_700_000_000,
        expires=1_700_000_060,
        nonce="AQIDBAUGBwgJCgsMDQ4PEA",
    )
    assert '"idempotency-key": operation-001' in signed["signature-base"].decode("ascii")
    assert '"content-encoding": gzip' in signed["signature-base"].decode("ascii")
    assert signed["content-digest"] == (
        "sha-256=:" + base64.b64encode(hashlib.sha256(b"\x1f\x8b\x08\x00").digest()).decode() + ":"
    )


def test_normative_vectors_match_python_signer_exactly():
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    for vector in json.loads(VECTORS.read_text())["vectors"]:
        body = base64.b64decode(vector["body_base64"])
        if vector["name"] == "gzip":
            assert body == gzip.compress(b'{"note":"test"}', mtime=0)
        signed = sign_request(
            private_key=key, key_id="fixture-key", method=vector["method"],
            url=vector["url"], body=body, created=1_700_000_000,
            expires=1_700_000_060, nonce="AQIDBAUGBwgJCgsMDQ4PEA",
            content_type=vector["headers"].get("content-type"),
            content_encoding=vector["headers"].get("content-encoding"),
            operation_id=vector["headers"].get("idempotency-key"),
        )
        assert signed.pop("signature-base") == vector["signature_base"].encode("ascii")
        assert signed == vector["headers"]


@pytest.mark.parametrize("change", [
    {"method": "HEAD"}, {"body": b"not empty"}, {"content_type": "application/json"},
    {"operation_id": "operation-1"}, {"content_encoding": "gzip"},
    {"nonce": "_" * 22}, {"expires": 1_700_000_000},
    {"url": "HTTPS://community.example/v1/lookup"},
    {"url": "https://COMMUNITY.example/v1/lookup"},
    {"url": "https://community.example:443/v1/lookup"},
    {"url": "https://community.example/v1/lookup#"},
    {"url": "https://community.example/v1/%2fadmin"},
    {"url": "https://community.example/v1/../admin"},
])
def test_signer_rejects_invalid_or_ambiguous_inputs(change):
    arguments = dict(
        private_key=Ed25519PrivateKey.from_private_bytes(bytes(range(32))),
        key_id="fixture-key", method="GET", url="https://community.example/v1/lookup",
        body=b"", created=1_700_000_000, expires=1_700_000_060,
        nonce="AQIDBAUGBwgJCgsMDQ4PEA",
    )
    with pytest.raises(SignatureProfileError):
        sign_request(**(arguments | change))
