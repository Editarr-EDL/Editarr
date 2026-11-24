"""Deterministic cross-language vectors for community master-set formats.

Run `uv run python -m scanner.testing.community_v3_vectors` to regenerate the files under
`community-service/test/fixtures/community-v3/`. Keys here are public test keys only.
"""

import base64
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scanner.formats.community.artifact_v3 import (
    build_manifest_v3,
    encode_artifact_v3,
    parse_manifest_v3,
)
from scanner.formats.community.artifact_v3_models import ArtifactV3
from scanner.formats.community.strict_json import canonical_json_bytes
from scanner.modeling.community_normalization import normalize, title_year_key
from scanner.security.community_artifact_signatures import signature_base
from scanner.security.community_artifact_v3_signatures import sign_artifact_v3
from scanner.security.community_master_snapshots import (
    MasterReceiptFields,
    SnapshotSignature,
    master_receipt_identity,
    master_receipt_signing_bytes,
    snapshot_signing_bytes,
)

FIXTURE_DIRECTORY = (
    Path(__file__).resolve().parents[2] / "community-service/test/fixtures/community-v3"
)
AUTHOR_SEED = bytes(range(32))
SERVICE_SEED = bytes(range(1, 33))
SERVICE = "https://community.example"
NORMALIZE_INPUTS = (
    "Director’s Cut",
    "  EXTENDED   Edition!",
    "Amélie",
    "Spider-Man: No Way Home",
    "Ⅲ",
    "Director's Cut",
    "Directorʼs`Cut",
    "ＡＢＣ　１２３",
    "Straße",
    "İstanbul",
    "Ǆemal",
    "Crème Brûlée",
    "Léon: The Professional",
    "WALL·E",
    "Se7en",
    "東京物語",
    "Ελληνικά",
    "",
    "!!!",
    "a\u0301\u0327",
)


def public_key(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def key_id(private: Ed25519PrivateKey) -> str:
    return "sha256:" + hashlib.sha256(public_key(private)).hexdigest()


def sign_master_message(private_key: Ed25519PrivateKey, message: bytes) -> str:
    """Sign a framed master message as the service would, for fixtures and tests."""
    return base64.b64encode(private_key.sign(message)).decode("ascii")


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def normalize_vector() -> dict:
    return {
        "description": "normalize() per contract §3.3; titleYear keys are normalize + NUL + year",
        "cases": [{"input": value, "normalized": normalize(value)} for value in NORMALIZE_INPUTS],
        "titleYear": [
            {"title": "The Matrix", "year": 1999, "key": title_year_key("The Matrix", 1999)},
            {"title": "Amélie", "year": 2001, "key": title_year_key("Amélie", 2001)},
        ],
    }


def artifact_object(author_key_id: str) -> dict:
    """Return the §5.2 example artifact with concrete, deterministic values."""
    return {
        "schemaVersion": "community-artifact-3",
        "submissionId": "sub_01J9Z3K5C6Q",
        "createdAt": 1795553648,
        "author": {"contributorId": "c_7f3a", "keyId": author_key_id},
        "work": {
            "kind": "movie",
            "tmdb": "603",
            "tvdb": None,
            "season": None,
            "episode": None,
            "lastEpisode": None,
            "title": "The Matrix",
            "year": 1999,
        },
        "version": {"choice": "alias", "aliasId": "theatrical"},
        "media": {
            "durationMs": 8160512,
            "matchKeys": [
                {"type": "sha256_sampled", "value": "a" * 64, "sizeBytes": 4823749120},
                {"type": "md5_quick", "value": "b" * 32, "sizeBytes": 4823749120},
                {
                    "type": "audio_stream",
                    "algorithm": "editarr-community-audio-pcm-s16le-16000-mono-sha256-1",
                    "value": "c" * 64,
                },
            ],
        },
        "alignmentHint": {
            "versionId": "v_12ab",
            "revision": 7,
            "offsetMs": -1200,
            "scale": 1.0,
            "anchors": 6,
        },
        "coverage": [{"category": "violence", "intervals": [{"startMs": 0, "endMs": 8160512}]}],
        "entries": [
            {
                "entryId": "e1",
                "kind": "support",
                "proposedFilterId": "f_9c1d",
                "startMs": 600000,
                "endMs": 605000,
                "category": "violence",
                "subcategory": "punching",
                "severity": "medium",
                "action": "skip",
                "evidence": {
                    "origin": "downloaded",
                    "manualReview": True,
                    "locallyVerified": True,
                    "modelVersion": None,
                },
            },
            {
                "entryId": "e2",
                "kind": "dispute",
                "proposedFilterId": "f_44aa",
                "startMs": 1200000,
                "endMs": 1203000,
                "category": "language",
                "subcategory": None,
                "severity": None,
                "action": None,
                "evidence": {
                    "origin": "model",
                    "manualReview": False,
                    "locallyVerified": False,
                    "modelVersion": "",
                },
            },
        ],
    }


def artifact_vector() -> dict:
    author = Ed25519PrivateKey.from_private_bytes(AUTHOR_SEED)
    value = artifact_object(key_id(author))
    decoded = encode_artifact_v3(ArtifactV3.model_validate(value, by_alias=True, by_name=False))
    manifest_bytes = build_manifest_v3(decoded)
    manifest = parse_manifest_v3(manifest_bytes, decoded=decoded)
    signed = sign_artifact_v3(
        private_key=author, artifact_bytes=decoded.gzip_bytes, manifest_bytes=manifest_bytes
    )
    return {
        "description": (
            "community-artifact-3 submission. canonicalJson is JSON.stringify of artifact; "
            "gzip bytes need not be reproduced byte-for-byte but must verify as given."
        ),
        "authorPrivateKeySeedHex": AUTHOR_SEED.hex(),
        "authorPublicKeyBase64": _b64(public_key(author)),
        "authorKeyId": key_id(author),
        "artifact": value,
        "canonicalJson": decoded.json_bytes.decode("utf-8"),
        "gzipBase64": _b64(decoded.gzip_bytes),
        "artifactDigest": decoded.digest,
        "manifest": manifest_bytes.decode("utf-8"),
        "signatureBaseHex": signature_base(manifest_bytes, manifest).hex(),
        "envelope": signed.envelope_bytes.decode("ascii"),
    }


def snapshot_object() -> dict:
    """Return the §7.3 example snapshot plus a merged, low-confidence second filter."""
    return {
        "schemaVersion": "community-master-snapshot-1",
        "service": SERVICE,
        "versionId": "v_12ab",
        "revision": 13,
        "generatedAt": 1795553600,
        "work": {
            "workId": "w_77",
            "kind": "movie",
            "tmdb": "603",
            "tvdb": None,
            "season": None,
            "episode": None,
            "lastEpisode": None,
            "title": "The Matrix",
            "year": 1999,
        },
        "versionInfo": {
            "status": "confirmed",
            "aliasId": "theatrical",
            "label": "Theatrical",
            "labelText": None,
            "runtimeMs": 8160500,
            "trust": 0.41,
        },
        "filters": [
            {
                "filterId": "f_9c1d",
                "mergedIds": [],
                "category": "violence",
                "subcategory": "punching",
                "severity": "high",
                "action": "skip",
                "startMs": 600000,
                "endMs": 605050,
                "spread": {"startMs": 300, "endMs": 350},
                "confidence": 0.393857102,
                "contributorCount": 4,
                "support": {"attestations": 4, "upvotes": 1},
                "negative": {"downvotes": 1, "disputes": 0, "coverageNegatives": 1},
                "lowConfidence": False,
            },
            {
                "filterId": "f_77aa",
                "mergedIds": ["f_old"],
                "category": "language",
                "subcategory": "swearing",
                "severity": "medium",
                "action": "mute",
                "startMs": 1200000,
                "endMs": 1203000,
                "spread": {"startMs": 0, "endMs": 0},
                "confidence": 0.117618231,
                "contributorCount": 1,
                "support": {"attestations": 1, "upvotes": 0},
                "negative": {"downvotes": 0, "disputes": 2, "coverageNegatives": 0},
                "lowConfidence": True,
            },
        ],
    }


def master_response(
    snapshot: bytes,
    *,
    signer: Ed25519PrivateKey,
    contributor_id: str = "c_7f3a",
    revision: int = 13,
    issued_at: int = 1795553648,
    receipt_changes: dict | None = None,
) -> dict:
    """Build a signed `GET /v1/versions/v_12ab/master` response for exact snapshot bytes."""
    digest = hashlib.sha256(snapshot).hexdigest()
    signature_fields = {
        "schemaVersion": "community-master-signature-1",
        "service": SERVICE,
        "serviceKeyId": key_id(signer),
        "versionId": "v_12ab",
        "revision": revision,
        "snapshotDigest": digest,
        "issuedAt": issued_at,
    }
    unsigned = SnapshotSignature.model_validate({**signature_fields, "signature": "A" * 88})
    receipt = {
        "schemaVersion": "community-master-receipt-1",
        "service": SERVICE,
        "serviceKeyId": key_id(signer),
        "contributorId": contributor_id,
        "versionId": "v_12ab",
        "revision": revision,
        "snapshotDigest": digest,
        "receiptId": master_receipt_identity(SERVICE, contributor_id, "v_12ab", revision, digest),
        "issuedAt": issued_at,
        **(receipt_changes or {}),
    }
    receipt_message = master_receipt_signing_bytes(MasterReceiptFields.model_validate(receipt))
    return {
        "version": 1,
        "changed": True,
        "snapshot": _b64(snapshot),
        "snapshotSignature": {
            **signature_fields,
            "signature": sign_master_message(signer, snapshot_signing_bytes(unsigned)),
        },
        "receipt": {"receipt": receipt, "signature": sign_master_message(signer, receipt_message)},
    }


def master_vector() -> dict:
    service_key = Ed25519PrivateKey.from_private_bytes(SERVICE_SEED)
    snapshot = canonical_json_bytes(snapshot_object())
    response = master_response(snapshot, signer=service_key)
    signed = SnapshotSignature.model_validate(response["snapshotSignature"])
    receipt = MasterReceiptFields.model_validate(response["receipt"]["receipt"])
    return {
        "description": "Newer-revision master response with snapshot signature and receipt (§7.3)",
        "servicePrivateKeySeedHex": SERVICE_SEED.hex(),
        "receiptKeys": [
            {
                "keyId": key_id(service_key),
                "publicKey": _b64(public_key(service_key)),
                "status": "active",
            },
        ],
        "request": {
            "service": SERVICE,
            "contributorId": "c_7f3a",
            "versionId": "v_12ab",
            "knownRevision": 12,
        },
        "snapshotJson": snapshot.decode("utf-8"),
        "snapshotDigest": signed.snapshot_digest,
        "snapshotSigningBytesHex": snapshot_signing_bytes(signed).hex(),
        "receiptId": receipt.receipt_id,
        "receiptSigningBytesHex": master_receipt_signing_bytes(receipt).hex(),
        "response": response,
    }


def vectors() -> dict[str, dict]:
    """Return generated vector documents keyed by file name."""
    return {
        "normalize.json": normalize_vector(),
        "artifact-v3.json": artifact_vector(),
        "master-snapshot.json": master_vector(),
    }


def render(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    FIXTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for name, document in vectors().items():
        (FIXTURE_DIRECTORY / name).write_text(render(document), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
