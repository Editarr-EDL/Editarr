import base64
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scanner.formats.community.codec import ResourceBounds, parse_artifact, parse_manifest
from scanner.security.community_artifact_signatures import (
    ArtifactSignatureError,
    HistoricalKey,
    sign_artifact,
    signature_base,
    verify_artifact_signature,
)

BOUNDS = ResourceBounds(4096, 16384, 10, 256, 12)
FIXTURES = Path(__file__).resolve().parents[1] / "community-service/test/fixtures"
# The retired v1 artifacts the service no longer ships; the signature framing is unchanged.
LEGACY = Path(__file__).resolve().parent / "fixtures/community"
VECTORS = json.loads((FIXTURES / "artifact-signatures-v1.json").read_text())


def original_bytes():
    artifact_bytes = base64.b64decode(
        json.loads((LEGACY / "artifact-v1-gzip.json").read_text())["base64"]
    )
    artifact = parse_artifact(artifact_bytes, bounds=BOUNDS, compressed=True)
    manifest_bytes = json.dumps(
        {
            "schema_version": "community-manifest-1",
            "artifact_format": "gzip-json",
            "artifact_schema_version": "community-artifact-1",
            "artifact_digest": hashlib.sha256(artifact_bytes).hexdigest(),
            "submission_id": artifact.artifact.submission_id,
            "contributor_id": artifact.artifact.author.contributor_id,
            "key_id": artifact.artifact.author.key_id,
            "summary": artifact.summary,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return artifact_bytes, manifest_bytes


def derived_bytes():
    original, _ = original_bytes()
    payload = json.loads((LEGACY / "artifact-v1.json").read_text())
    parent = {"submission_id": "submission-1", "digest": hashlib.sha256(original).hexdigest()}
    payload["submission_id"] = "derived-1"
    payload["kind"] = "derived"
    payload["author"] = {"contributor_id": "contributor-2", "key_id": "key-2"}
    payload["media"]["timeline"] = "derived_target"
    payload["source_references"] = [parent]
    payload["derivation"] = {"parents": [parent], "transform": "manual retiming"}
    payload["findings"][0]["source"] = "derived"
    payload["findings"][0]["parent_event_id"] = "event-1"
    return gzip.compress(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0
    )


@pytest.mark.parametrize("name,seed", [("original", range(32)), ("derived", range(32, 64))])
def test_frozen_vectors_are_signed_by_the_python_codec(name, seed):
    expected = VECTORS[name]
    artifact_bytes = original_bytes()[0] if name == "original" else derived_bytes()
    if name == "derived":
        assert artifact_bytes == base64.b64decode("".join(expected["artifact_base64_chunks"]))
    assert hashlib.sha256(artifact_bytes).hexdigest() == expected["artifact_sha256"]
    artifact = parse_artifact(artifact_bytes, bounds=BOUNDS, compressed=True)
    summary = artifact.summary
    summary["duration_seconds"] = 12
    manifest_bytes = json.dumps(
        {
            "schema_version": "community-manifest-1",
            "artifact_format": "gzip-json",
            "artifact_schema_version": "community-artifact-1",
            "artifact_digest": expected["artifact_sha256"],
            "submission_id": artifact.artifact.submission_id,
            "contributor_id": artifact.artifact.author.contributor_id,
            "key_id": artifact.artifact.author.key_id,
            "summary": summary,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(manifest_bytes).hexdigest() == expected["manifest_sha256"]
    manifest = parse_manifest(manifest_bytes, artifact=artifact, bounds=BOUNDS)
    base = signature_base(manifest_bytes, manifest.manifest)
    assert hashlib.sha256(base).hexdigest() == expected["base_sha256"]
    signed = sign_artifact(
        private_key=Ed25519PrivateKey.from_private_bytes(bytes(seed)),
        artifact_bytes=artifact_bytes,
        manifest_bytes=manifest_bytes,
        bounds=BOUNDS,
    )
    assert json.loads(signed.envelope_bytes)["signature"] == expected["signature_base64"]
    verified = verify_artifact_signature(
        artifact_bytes=artifact_bytes,
        manifest_bytes=manifest_bytes,
        envelope_bytes=signed.envelope_bytes,
        historical_key=HistoricalKey(
            artifact.artifact.author.contributor_id,
            artifact.artifact.author.key_id,
            Ed25519PrivateKey.from_private_bytes(bytes(seed)).public_key().public_bytes_raw(),
            "retired",
        ),
        expected_submission_id=artifact.artifact.submission_id,
        bounds=BOUNDS,
    )
    assert verified.signed == signed
    assert verified.artifact.artifact == artifact.artifact
    if name == "derived":
        assert (
            artifact.artifact.source_references[0].digest == VECTORS["original"]["artifact_sha256"]
        )


def test_detached_signature_binds_exact_original_bytes():
    artifact_bytes, manifest_bytes = original_bytes()
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    signed = sign_artifact(
        private_key=private_key,
        artifact_bytes=artifact_bytes,
        manifest_bytes=manifest_bytes,
        bounds=BOUNDS,
    )
    assert signed.artifact_bytes == artifact_bytes
    assert signed.manifest_bytes == manifest_bytes
    assert signed.artifact_bytes is artifact_bytes
    assert signed.manifest_bytes is manifest_bytes
    envelope = json.loads(signed.envelope_bytes)
    assert envelope["schema_version"] == "community-artifact-signature-1"
    manifest = parse_manifest(
        manifest_bytes,
        artifact=parse_artifact(artifact_bytes, bounds=BOUNDS, compressed=True),
        bounds=BOUNDS,
    ).manifest
    base = signature_base(manifest_bytes, manifest)
    signature = base64.b64decode(envelope["signature"])
    private_key.public_key().verify(signature, base)
    changed = manifest_bytes.replace(b'"submission_id"', b' "submission_id"', 1)
    assert parse_manifest(
        changed,
        artifact=parse_artifact(artifact_bytes, bounds=BOUNDS, compressed=True),
        bounds=BOUNDS,
    )
    with pytest.raises(InvalidSignature):
        private_key.public_key().verify(signature, signature_base(changed, manifest))


def test_signer_refuses_mismatched_manifest_or_payload():
    artifact_bytes, manifest_bytes = original_bytes()
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    with pytest.raises(ValueError):
        sign_artifact(
            private_key=private_key,
            artifact_bytes=artifact_bytes + b"x",
            manifest_bytes=manifest_bytes,
            bounds=BOUNDS,
        )
    with pytest.raises(ValueError):
        sign_artifact(
            private_key=private_key,
            artifact_bytes=artifact_bytes,
            manifest_bytes=manifest_bytes.replace(b"contributor-1", b"contributor-2"),
            bounds=BOUNDS,
        )


@pytest.mark.parametrize(
    "change",
    [
        "author",
        "key",
        "submission",
        "revoked",
        "compromised",
        "public_key",
        "manifest",
        "signature",
        "envelope",
    ],
)
def test_verifier_rejects_untrusted_bindings_and_altered_original_bytes(change):
    artifact_bytes, manifest_bytes = original_bytes()
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    signed = sign_artifact(
        private_key=private_key,
        artifact_bytes=artifact_bytes,
        manifest_bytes=manifest_bytes,
        bounds=BOUNDS,
    )
    key = HistoricalKey(
        "other" if change == "author" else "contributor-1",
        "other" if change == "key" else "key-1",
        bytes(32) if change == "public_key" else private_key.public_key().public_bytes_raw(),
        change if change in {"revoked", "compromised"} else "active",
    )
    envelope = signed.envelope_bytes
    if change == "manifest":
        manifest_bytes = manifest_bytes.replace(b'"submission_id"', b' "submission_id"', 1)
    if change == "signature":
        envelope = json.dumps(
            {
                "schema_version": "community-artifact-signature-1",
                "signature": base64.b64encode(bytes(64)).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode()
    if change == "envelope":
        envelope += b" "
    with pytest.raises(ArtifactSignatureError):
        verify_artifact_signature(
            artifact_bytes=artifact_bytes,
            manifest_bytes=manifest_bytes,
            envelope_bytes=envelope,
            historical_key=key,
            expected_submission_id="other" if change == "submission" else "submission-1",
            bounds=BOUNDS,
        )
