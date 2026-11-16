"""Exact, service-bound dual proofs for an explicitly prepared key rotation."""

import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .private_identity_files import IdentityUnavailable


def proof_bytes(role, audience, operation_id, installation_id, old_id, new_id, public):
    return json.dumps(
        [
            "editarr-key-rotation-v1",
            role,
            audience,
            operation_id,
            installation_id,
            old_id,
            new_id,
            public,
        ],
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def build_rotation_body(identity, audience):
    """Freeze both signatures using the already persisted replacement, never a new key."""
    pending = identity.pending_replacement
    if pending is None:
        raise IdentityUnavailable("pending", "No prepared replacement")
    public = base64.b64encode(pending.public_key).decode("ascii")
    fields = (
        audience,
        pending.operation_id,
        identity.installation_id,
        identity.key_id,
        pending.key_id,
        public,
    )
    return json.dumps(
        {
            "version": 1,
            "installationId": identity.installation_id,
            "oldKeyId": identity.key_id,
            "newPublicKey": public,
            "oldProof": base64.b64encode(
                identity.private_key.sign(proof_bytes("old", *fields))
            ).decode("ascii"),
            "newProof": base64.b64encode(
                pending.private_key.sign(proof_bytes("new", *fields))
            ).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("ascii")


def validate_rotation_body(
    body, audience, operation_id, installation_id, old_id, old_public, new_id, new_public
):
    """Verify a retained journal against both public keys and its immutable operation."""
    try:
        if not isinstance(body, str) or len(body.encode("ascii")) > 2048:
            raise ValueError
        value = json.loads(body)
        public = base64.b64encode(new_public).decode("ascii")
        if (
            set(value)
            != {"version", "installationId", "oldKeyId", "newPublicKey", "oldProof", "newProof"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or value["installationId"] != installation_id
            or value["oldKeyId"] != old_id
            or value["newPublicKey"] != public
        ):
            raise ValueError
        fields = (audience, operation_id, installation_id, old_id, new_id, public)
        for role, key in (("old", old_public), ("new", new_public)):
            encoded = value[role + "Proof"]
            signature = base64.b64decode(encoded, validate=True)
            if base64.b64encode(signature).decode("ascii") != encoded:
                raise ValueError
            Ed25519PublicKey.from_public_bytes(key).verify(signature, proof_bytes(role, *fields))
    except (ValueError, TypeError, KeyError, UnicodeError, InvalidSignature) as error:
        raise IdentityUnavailable("corrupt", "Invalid retained rotation proofs") from error


def validate_rotation_result(
    result, *, operation_id, contributor_id, installation_id, old_id, new_id
):
    expected = {
        "version": 1,
        "kind": "key_rotation",
        "operationId": operation_id,
        "contributorId": contributor_id,
        "installationId": installation_id,
        "oldKeyId": old_id,
        "newKeyId": new_id,
    }
    if (
        not isinstance(result, dict)
        or set(result) != set(expected) | {"committedAt"}
        or any(result.get(name) != value for name, value in expected.items())
        or type(result.get("version")) is not int
        or type(result.get("committedAt")) is not int
        or result["committedAt"] < 0
    ):
        raise IdentityUnavailable("corrupt", "Invalid rotation outcome")
