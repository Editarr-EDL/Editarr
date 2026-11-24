import base64
import copy
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scanner.formats.community.master_snapshot import MasterSnapshotError, parse_master_snapshot
from scanner.security.community_download_receipts import ReceiptKey
from scanner.security.community_master_snapshots import (
    MasterReceiptFields,
    MasterUnchanged,
    MasterVerificationError,
    SnapshotSignature,
    master_receipt_identity,
    master_receipt_signing_bytes,
    sign_master_message,
    snapshot_signing_bytes,
    verify_master_response,
)

SERVICE = "https://community.example"
SERVICE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
OTHER_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(2, 34)))


def key_record(private: Ed25519PrivateKey, status: str = "active") -> ReceiptKey:
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return ReceiptKey.model_validate(
        {
            "keyId": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "publicKey": base64.b64encode(raw).decode(),
            "status": status,
        }
    )


SERVICE_KEY_ID = key_record(SERVICE_KEY).key_id


def snapshot_value() -> dict:
    return {
        "schemaVersion": "community-master-snapshot-1", "service": SERVICE,
        "versionId": "v_12ab", "revision": 13, "generatedAt": 1795553600,
        "work": {
            "workId": "w_77", "kind": "movie", "tmdb": "603", "tvdb": None, "season": None,
            "episode": None, "lastEpisode": None, "title": "The Matrix", "year": 1999,
        },
        "versionInfo": {
            "status": "confirmed", "aliasId": "theatrical", "label": "Theatrical",
            "labelText": None, "runtimeMs": 8160500, "trust": 0.41,
        },
        "filters": [
            {
                "filterId": "f_9c1d", "mergedIds": [], "category": "violence",
                "subcategory": "punching", "severity": "high", "action": "skip",
                "startMs": 600000, "endMs": 605050, "spread": {"startMs": 300, "endMs": 350},
                "confidence": 0.393857102, "contributorCount": 4,
                "support": {"attestations": 4, "upvotes": 1},
                "negative": {"downvotes": 1, "disputes": 0, "coverageNegatives": 1},
                "lowConfidence": False,
            },
            {
                "filterId": "f_77aa", "mergedIds": ["f_old"], "category": "language",
                "subcategory": "swearing", "severity": "medium", "action": "mute",
                "startMs": 1200000, "endMs": 1203000, "spread": {"startMs": 0, "endMs": 0},
                "confidence": 0.117618231, "contributorCount": 1,
                "support": {"attestations": 1, "upvotes": 0},
                "negative": {"downvotes": 0, "disputes": 2, "coverageNegatives": 0},
                "lowConfidence": True,
            },
        ],
    }


def encode(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def response(
    snapshot: bytes | None = None,
    *,
    signer: Ed25519PrivateKey = SERVICE_KEY,
    contributor_id: str = "c_7f3a",
    revision: int = 13,
    receipt_changes: dict | None = None,
) -> dict:
    snapshot = encode(snapshot_value()) if snapshot is None else snapshot
    digest = hashlib.sha256(snapshot).hexdigest()
    key_id = key_record(signer).key_id
    signature_fields = {
        "schemaVersion": "community-master-signature-1", "service": SERVICE,
        "serviceKeyId": key_id, "versionId": "v_12ab", "revision": revision,
        "snapshotDigest": digest, "issuedAt": 1795553648,
    }
    signed = SnapshotSignature.model_validate({**signature_fields, "signature": "A" * 88})
    receipt = {
        "schemaVersion": "community-master-receipt-1", "service": SERVICE,
        "serviceKeyId": key_id, "contributorId": contributor_id, "versionId": "v_12ab",
        "revision": revision, "snapshotDigest": digest,
        "receiptId": master_receipt_identity(SERVICE, contributor_id, "v_12ab", revision, digest),
        "issuedAt": 1795553648, **(receipt_changes or {}),
    }
    receipt_signature = sign_master_message(
        signer, master_receipt_signing_bytes(MasterReceiptFields.model_validate(receipt))
    )
    return {
        "version": 1, "changed": True, "snapshot": base64.b64encode(snapshot).decode(),
        "snapshotSignature": {
            **signature_fields,
            "signature": sign_master_message(signer, snapshot_signing_bytes(signed)),
        },
        "receipt": {"receipt": receipt, "signature": receipt_signature},
    }


def verify(value: object, known_revision: int | None = 12, **changes):
    arguments = {
        "service": SERVICE, "contributor_id": "c_7f3a", "version_id": "v_12ab",
        "known_revision": known_revision,
        "trusted_keys": (key_record(SERVICE_KEY), key_record(OTHER_KEY, "revoked")),
        **changes,
    }
    return verify_master_response(value, **arguments)


def test_signed_bytes_follow_the_framing():
    signed = SnapshotSignature.model_validate({
        "schemaVersion": "community-master-signature-1", "service": SERVICE,
        "serviceKeyId": SERVICE_KEY_ID, "versionId": "v_12ab", "revision": 13,
        "snapshotDigest": "a" * 64, "issuedAt": 1795553648, "signature": "A" * 88,
    })
    message = snapshot_signing_bytes(signed)
    assert message.startswith(
        b"editarr-community-master-snapshot-v1\0\0\0\0\x1ccommunity-master-signature-1"
    )
    assert b"\0\0\0\x0213\0\0\0\x40" + b"a" * 64 in message
    assert message.endswith((1795553648).to_bytes(8, "big"))
    identity = master_receipt_identity(SERVICE, "c_7f3a", "v_12ab", 13, "a" * 64)
    fields = (SERVICE, "c_7f3a", "v_12ab", "13", "a" * 64)
    framed = b"editarr-community-master-receipt-identity-v1\0" + b"".join(
        len(field).to_bytes(4, "big") + field.encode() for field in fields
    )
    assert identity == hashlib.sha256(framed).hexdigest()


def test_verified_newer_revision():
    verified = verify(response())
    assert verified.snapshot.revision == 13
    assert verified.snapshot.filters[1].merged_ids == ["f_old"]
    assert verified.snapshot_bytes == encode(snapshot_value())
    assert verify(response(), known_revision=None).snapshot.version_id == "v_12ab"
    retired = (key_record(SERVICE_KEY, "retired"),)
    assert verify(response(), trusted_keys=retired).receipt.receipt.revision == 13


def test_not_newer_response():
    body = {"version": 1, "changed": False, "versionId": "v_12ab", "revision": 13}
    assert verify(body, known_revision=13) == MasterUnchanged("v_12ab", 13)
    for value, known in (
        (body, 12),
        (body, None),
        ({**body, "versionId": "v_other"}, 13),
        ({**body, "extra": 1}, 13),
    ):
        with pytest.raises(MasterVerificationError):
            verify(value, known_revision=known)


def tampered_responses():
    good = response()
    yield "stale revision", good, {"known_revision": 13}
    yield "wrong version", good, {"version_id": "v_other"}
    yield "wrong service", good, {"service": "https://other.example"}
    yield "noncanonical service", good, {"service": "https://community.example/"}
    yield "wrong contributor", good, {"contributor_id": "c_other"}
    yield "unknown key", good, {"trusted_keys": (key_record(OTHER_KEY),)}
    yield "revoked signer", response(signer=OTHER_KEY), {}
    changed = copy.deepcopy(good)
    changed["snapshot"] = base64.b64encode(encode({**snapshot_value(), "revision": 14})).decode()
    yield "snapshot bytes changed", changed, {}
    unpadded = copy.deepcopy(good)
    unpadded["snapshot"] = base64.b64encode(encode(snapshot_value()) + b" ").decode().rstrip("=")
    yield "noncanonical base64", unpadded, {}
    signature = copy.deepcopy(good)
    signature["snapshotSignature"]["issuedAt"] += 1
    yield "signature fields changed", signature, {}
    revision = snapshot_value()
    revision["revision"] = 12
    yield "snapshot revision differs from signature", response(encode(revision)), {}
    receipt = copy.deepcopy(good)
    receipt["receipt"]["signature"] = good["snapshotSignature"]["signature"]
    yield "receipt signature", receipt, {}
    yield "receipt identity", response(receipt_changes={"receiptId": "0" * 64}), {}
    yield "receipt digest", response(receipt_changes={"snapshotDigest": "0" * 64}), {}
    other_key = response(receipt_changes={"serviceKeyId": key_record(OTHER_KEY).key_id})
    yield "receipt key differs", other_key, {}
    extra = copy.deepcopy(good)
    extra["receipt"]["receipt"]["extra"] = 1
    yield "unknown receipt field", extra, {}
    invalid = snapshot_value()
    invalid["filters"].reverse()
    yield "invalid snapshot", response(encode(invalid)), {}


@pytest.mark.parametrize("case", list(tampered_responses()), ids=lambda case: case[0])
def test_tampered_master_responses_are_rejected(case):
    _, value, changes = case
    with pytest.raises(MasterVerificationError):
        verify(value, **changes)


def snapshot_mutations():
    def change(callback):
        value = snapshot_value()
        callback(value)
        return value

    yield "duplicate key", encode(snapshot_value()).replace(
        b'"revision":13', b'"revision":13,"revision":13'
    )
    yield "unknown field", encode(change(lambda v: v.update(extra=1)))
    yield "unsorted", encode(change(lambda v: v["filters"].reverse()))
    yield "duplicate filter", encode(change(lambda v: v["filters"][1].update(filterId="f_9c1d")))
    yield "merged id reused", encode(change(lambda v: v["filters"][1].update(mergedIds=["f_9c1d"])))
    yield "taxonomy", encode(change(lambda v: v["filters"][0].update(subcategory="swearing")))
    yield "bounds", encode(change(lambda v: v["filters"][0].update(endMs=600000)))
    yield "count", encode(change(lambda v: v["filters"][0].update(contributorCount=3)))
    yield "confidence", encode(change(lambda v: v["filters"][0].update(confidence=1.5)))
    yield "status", encode(change(lambda v: v["versionInfo"].update(status="merged_into")))
    yield "no identity", encode(change(lambda v: v["work"].update(tmdb=None)))
    yield "movie numbering", encode(change(lambda v: v["work"].update(season=1)))
    yield "float revision", encode(change(lambda v: v.update(revision=13.0)))
    yield "too large", encode(snapshot_value()) + b" " * 2_000_000


@pytest.mark.parametrize("case", list(snapshot_mutations()), ids=lambda case: case[0])
def test_snapshot_strict_parse_failures(case):
    with pytest.raises(MasterSnapshotError):
        parse_master_snapshot(case[1])


def test_snapshot_filter_limit():
    raw = encode(snapshot_value())
    assert len(parse_master_snapshot(raw).filters) == 2
    with pytest.raises(MasterSnapshotError):
        parse_master_snapshot(raw, max_filters=1)
