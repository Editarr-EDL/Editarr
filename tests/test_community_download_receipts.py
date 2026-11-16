"""Python consumers reproduce the independently generated CSS-42 receipt vector."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scanner.security.community_download_receipts import (
    ReceiptError,
    parse_receipt_keys,
    receipt_signing_bytes,
    verify_download_receipt,
)

VECTOR = json.loads(
    (Path(__file__).parent / "fixtures/community/download-receipt-v1.json").read_text()
)


def verify(value=None, *, status="active", **changes):
    receipt = VECTOR["envelope"]["receipt"]
    expected = dict(
        service=receipt["service"],
        contributor_id=receipt["contributorId"],
        revision_id=receipt["revisionId"],
        artifact_digest=receipt["artifactDigest"],
    )
    expected.update(changes)
    return verify_download_receipt(
        VECTOR["envelope"] if value is None else value,
        trusted_keys=parse_receipt_keys(
            [
                {
                    "keyId": receipt["serviceKeyId"],
                    "publicKey": VECTOR["publicKey"],
                    "status": status,
                }
            ]
        ),
        **expected,
    )


@pytest.mark.parametrize("status", ["active", "retired"])
def test_frozen_receipt_verifies_without_expiry(status):
    verified = verify(status=status)
    assert verified.model_dump(by_alias=True) == VECTOR["envelope"]
    assert (
        hashlib.sha256(receipt_signing_bytes(verified.receipt)).hexdigest()
        == VECTOR["signingBytesSha256"]
    )


@pytest.mark.parametrize("change", ["service", "contributor_id", "revision_id", "artifact_digest"])
def test_wrong_receipt_bindings_fail(change):
    with pytest.raises(ReceiptError):
        verify(**{change: "other"})


@pytest.mark.parametrize(
    "change", ["revoked", "signature", "unknown_field", "issue_time", "identity"]
)
def test_hostile_receipts_fail(change):
    value = copy.deepcopy(VECTOR["envelope"])
    if change == "signature":
        value["signature"] = "A" * 86 + "=="
    elif change == "unknown_field":
        value["receipt"]["local_path"] = "/tmp/remote"
    elif change == "issue_time":
        value["receipt"]["issuedAt"] = True
    elif change == "identity":
        value["receipt"]["receiptId"] = "b" * 64
    with pytest.raises(ReceiptError):
        verify(value, status="revoked" if change == "revoked" else "active")
