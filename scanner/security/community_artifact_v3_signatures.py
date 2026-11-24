"""Sign and verify `community-artifact-3` submissions with the unchanged v1 envelope."""

import base64
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from scanner.formats.community.artifact_v3 import (
    DEFAULT_LIMITS,
    ArtifactV3Error,
    ArtifactV3Limits,
    DecodedArtifactV3,
    build_manifest_v3,
    decode_artifact_v3,
    encode_artifact_v3,
    parse_manifest_v3,
)
from scanner.formats.community.artifact_v3_models import ArtifactV3, ManifestV3

from .community_artifact_signatures import (
    ArtifactSignatureError,
    HistoricalKey,
    SignedArtifact,
    decode_signature_envelope,
    encode_signature_envelope,
    signature_base,
)


@dataclass(frozen=True)
class VerifiedArtifactV3:
    decoded: DecodedArtifactV3
    manifest: ManifestV3
    signed: SignedArtifact
    historical_key: HistoricalKey


def sign_artifact_v3(
    *,
    private_key: Ed25519PrivateKey,
    artifact_bytes: bytes,
    manifest_bytes: bytes,
    limits: ArtifactV3Limits = DEFAULT_LIMITS,
) -> SignedArtifact:
    """Sign validated original gzip and manifest bytes without reserializing them."""
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("Expected Ed25519 private key")
    decoded = decode_artifact_v3(artifact_bytes, limits=limits)
    manifest = parse_manifest_v3(manifest_bytes, decoded=decoded, limits=limits)
    signature = private_key.sign(signature_base(manifest_bytes, manifest))
    return SignedArtifact(artifact_bytes, manifest_bytes, encode_signature_envelope(signature))


def build_signed_submission(
    artifact: ArtifactV3,
    *,
    private_key: Ed25519PrivateKey,
    limits: ArtifactV3Limits = DEFAULT_LIMITS,
) -> SignedArtifact:
    """Encode an artifact, build its manifest and sign both, ready for `POST /v1/submissions`."""
    decoded = encode_artifact_v3(artifact, limits=limits)
    return sign_artifact_v3(
        private_key=private_key,
        artifact_bytes=decoded.gzip_bytes,
        manifest_bytes=build_manifest_v3(decoded),
        limits=limits,
    )


def submission_request_body(signed: SignedArtifact, submission_id: str) -> dict:
    """Return the `POST /v1/submissions` JSON body for exact signed buffers (§7.2)."""

    def encode(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    return {
        "version": 1,
        "submissionId": submission_id,
        "artifact": encode(signed.artifact_bytes),
        "manifest": encode(signed.manifest_bytes),
        "envelope": encode(signed.envelope_bytes),
    }


def verify_artifact_v3_signature(
    *,
    artifact_bytes: bytes,
    manifest_bytes: bytes,
    envelope_bytes: bytes,
    historical_key: HistoricalKey,
    expected_submission_id: str,
    limits: ArtifactV3Limits = DEFAULT_LIMITS,
) -> VerifiedArtifactV3:
    """Verify exact v3 buffers against an independently trusted author key binding.

    `active` and `retired` keys verify historical attribution; revoked keys never do.
    """
    if historical_key.status not in {"active", "retired"}:
        raise ArtifactSignatureError("Key not trusted for historical attribution")
    if len(historical_key.public_key) != 32:
        raise ArtifactSignatureError("Invalid public key")
    try:
        decoded = decode_artifact_v3(artifact_bytes, limits=limits)
        manifest = parse_manifest_v3(manifest_bytes, decoded=decoded, limits=limits)
    except ArtifactV3Error as exc:
        raise ArtifactSignatureError(f"Invalid signed artifact: {exc}") from exc
    if (
        manifest.contributor_id != historical_key.contributor_id
        or manifest.key_id != historical_key.key_id
        or manifest.submission_id != expected_submission_id
    ):
        raise ArtifactSignatureError("Historical key or submission binding mismatch")
    signature = decode_signature_envelope(envelope_bytes)
    try:
        Ed25519PublicKey.from_public_bytes(historical_key.public_key).verify(
            signature, signature_base(manifest_bytes, manifest)
        )
    except InvalidSignature as exc:
        raise ArtifactSignatureError("Detached signature does not match original bytes") from exc
    return VerifiedArtifactV3(
        decoded,
        manifest,
        SignedArtifact(artifact_bytes, manifest_bytes, envelope_bytes),
        historical_key,
    )
