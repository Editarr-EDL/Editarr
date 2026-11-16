"""Verify the hosted rotation proof vector independently for the future local client."""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures/community/key-rotation-v1.json").read_text()
)


@pytest.mark.parametrize("role", ["old", "new"])
def test_rotation_proof_bytes_and_signatures(role):
    intent = CONTRACT["intent"]
    payload = json.dumps(
        [
            "editarr-key-rotation-v1",
            role,
            CONTRACT["authority"],
            CONTRACT["operationId"],
            intent["installationId"],
            intent["oldKeyId"],
            CONTRACT["newKeyId"],
            intent["newPublicKey"],
        ],
        separators=(",", ":"),
    )
    assert payload == CONTRACT["proofMessages"][role]
    encoded_key = CONTRACT["oldPublicKey"] if role == "old" else intent["newPublicKey"]
    public_bytes = base64.b64decode(encoded_key, validate=True)
    key_id = intent["oldKeyId"] if role == "old" else CONTRACT["newKeyId"]
    assert key_id == "sha256:" + hashlib.sha256(public_bytes).hexdigest()
    key = Ed25519PublicKey.from_public_bytes(public_bytes)
    signature = base64.b64decode(intent[role + "Proof"], validate=True)
    key.verify(signature, payload.encode())
    with pytest.raises(InvalidSignature):
        key.verify(signature, payload.replace(CONTRACT["operationId"], "other-operation").encode())
    with pytest.raises(InvalidSignature):
        key.verify(signature, payload.replace(CONTRACT["authority"], "other.example").encode())
    assert json.loads(CONTRACT["requestBody"]) == intent


def test_local_proof_module_matches_hosted_vector():
    from scanner.security.community_rotation_proofs import proof_bytes, validate_rotation_body

    intent = CONTRACT["intent"]
    for role in ("old", "new"):
        assert (
            proof_bytes(
                role,
                CONTRACT["authority"],
                CONTRACT["operationId"],
                intent["installationId"],
                intent["oldKeyId"],
                CONTRACT["newKeyId"],
                intent["newPublicKey"],
            ).decode()
            == CONTRACT["proofMessages"][role]
        )
    validate_rotation_body(
        CONTRACT["requestBody"],
        CONTRACT["authority"],
        CONTRACT["operationId"],
        intent["installationId"],
        intent["oldKeyId"],
        base64.b64decode(CONTRACT["oldPublicKey"]),
        CONTRACT["newKeyId"],
        base64.b64decode(intent["newPublicKey"]),
    )
