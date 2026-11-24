"""Durable detached Ed25519 signatures over exact community artifact bytes."""

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from scanner.formats.community.codec import (
    DecodedArtifact,
    DecodedManifest,
    ResourceBounds,
    parse_artifact,
    parse_manifest,
)

DOMAIN = b"editarr-community-artifact-signature-v1\0"
ENVELOPE_VERSION = "community-artifact-signature-1"


@dataclass(frozen=True)
class SignedArtifact:
    artifact_bytes: bytes
    manifest_bytes: bytes
    envelope_bytes: bytes


class ArtifactSignatureError(ValueError):
    """Original bytes or their trusted historical author binding cannot be verified.

    `code` is the service error the failure corresponds to (§7.2).
    """

    def __init__(self, message: str = "", code: str = "invalid_submission") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HistoricalKey:
    contributor_id: str
    key_id: str
    public_key: bytes
    status: str


@dataclass(frozen=True)
class VerifiedArtifact:
    artifact: DecodedArtifact
    manifest: DecodedManifest
    signed: SignedArtifact
    historical_key: HistoricalKey


def verify_artifact_signature(
    *,
    artifact_bytes: bytes,
    manifest_bytes: bytes,
    envelope_bytes: bytes,
    historical_key: HistoricalKey,
    expected_submission_id: str,
    bounds: ResourceBounds,
) -> VerifiedArtifact:
    """Verify original bytes using an independently trusted author/key/submission binding."""
    if historical_key.status not in {"active", "retired"}:
        raise ArtifactSignatureError("Key not trusted for historical attribution")
    if len(historical_key.public_key) != 32:
        raise ArtifactSignatureError("Invalid public key")
    artifact = parse_artifact(artifact_bytes, bounds=bounds, compressed=True)
    manifest = parse_manifest(manifest_bytes, artifact=artifact, bounds=bounds)
    if (
        manifest.manifest.contributor_id != historical_key.contributor_id
        or manifest.manifest.key_id != historical_key.key_id
        or manifest.manifest.submission_id != expected_submission_id
    ):
        raise ArtifactSignatureError("Historical key or submission binding mismatch")
    signature = decode_signature_envelope(envelope_bytes)
    try:
        Ed25519PublicKey.from_public_bytes(historical_key.public_key).verify(
            signature, signature_base(manifest_bytes, manifest.manifest)
        )
    except InvalidSignature as exc:
        raise ArtifactSignatureError("Detached signature does not match original bytes") from exc
    return VerifiedArtifact(
        artifact,
        manifest,
        SignedArtifact(artifact_bytes, manifest_bytes, envelope_bytes),
        historical_key,
    )


class ManifestIdentity(Protocol):
    """Manifest fields covered by `editarr-community-artifact-signature-v1`."""

    schema_version: str
    artifact_schema_version: str
    artifact_format: str
    submission_id: str
    contributor_id: str
    key_id: str
    artifact_digest: str


def encode_signature_envelope(signature: bytes) -> bytes:
    """Return the exact `community-artifact-signature-1` envelope for 64 signature bytes."""
    if len(signature) != 64:
        raise ValueError("Expected a 64-byte Ed25519 signature")
    return json.dumps(
        {
            "schema_version": ENVELOPE_VERSION,
            "signature": base64.b64encode(signature).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("ascii")


def decode_signature_envelope(envelope_bytes: bytes) -> bytes:
    """Return the signature from a canonical envelope; any other spelling is rejected."""
    if not envelope_bytes or len(envelope_bytes) > 256:
        raise ArtifactSignatureError("Invalid detached signature envelope length")
    try:
        text = envelope_bytes.decode("utf-8")
        envelope = json.loads(text)
        if not isinstance(envelope, dict) or envelope.get("schema_version") != ENVELOPE_VERSION:
            raise ValueError
        encoded = envelope.get("signature")
        if not isinstance(encoded, str):
            raise ValueError
        signature = base64.b64decode(encoded, validate=True)
        if len(signature) != 64 or base64.b64encode(signature).decode("ascii") != encoded:
            raise ValueError
        if envelope_bytes != encode_signature_envelope(signature):
            raise ValueError
    except (UnicodeError, ValueError) as exc:
        raise ArtifactSignatureError("Noncanonical detached signature envelope") from exc
    return signature


def signature_base(manifest_bytes: bytes, manifest: ManifestIdentity) -> bytes:
    """Encode versioned identity fields and both SHA-256 hashes without JSON normalization."""
    fields = (
        manifest.schema_version,
        manifest.artifact_schema_version,
        manifest.artifact_format,
        manifest.submission_id,
        manifest.contributor_id,
        manifest.key_id,
    )
    encoded = [field.encode("utf-8") for field in fields]
    return (
        DOMAIN
        + b"".join(len(field).to_bytes(4, "big") + field for field in encoded)
        + hashlib.sha256(manifest_bytes).digest()
        + bytes.fromhex(manifest.artifact_digest)
    )


def sign_artifact(
    *,
    private_key: Ed25519PrivateKey,
    artifact_bytes: bytes,
    manifest_bytes: bytes,
    bounds: ResourceBounds,
) -> SignedArtifact:
    """Sign validated original gzip/manifest bytes; never reserialize their contents."""
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("Expected Ed25519 private key")
    artifact = parse_artifact(artifact_bytes, bounds=bounds, compressed=True)
    manifest = parse_manifest(manifest_bytes, artifact=artifact, bounds=bounds)
    signature = private_key.sign(signature_base(manifest.stored_bytes, manifest.manifest))
    envelope = encode_signature_envelope(signature)
    return SignedArtifact(artifact.stored_bytes, manifest.stored_bytes, envelope)
