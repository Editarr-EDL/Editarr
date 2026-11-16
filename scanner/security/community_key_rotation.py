"""Atomic private rotation journal and generation publication."""

import base64
import copy
import hashlib
import json
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .community_rotation_proofs import (
    build_rotation_body,
    validate_rotation_body,
    validate_rotation_result,
)
from .private_identity_files import IdentityHome, IdentityUnavailable


def validate_rotation_journal(manifest):
    record = manifest.get("rotation")
    if record is None:
        return
    try:
        if (
            set(record)
            != {
                "version",
                "state",
                "audience",
                "operationId",
                "oldKeyId",
                "oldPublicKey",
                "newKeyId",
                "newPublicKey",
                "body",
                "candidatePrivateKey",
                "result",
            }
            or record["version"] != 1
        ):
            raise ValueError
        if str(uuid.UUID(record["operationId"])) != record["operationId"]:
            raise ValueError
        from scanner.integrations.community.transport import ServiceEndpoint

        ServiceEndpoint(manifest["service_url"], record["audience"])
        old_public = base64.b64decode(record["oldPublicKey"], validate=True)
        new_public = base64.b64decode(record["newPublicKey"], validate=True)
        for key_id, public in ((record["oldKeyId"], old_public), (record["newKeyId"], new_public)):
            if key_id != "sha256:" + hashlib.sha256(public).hexdigest():
                raise ValueError
        validate_rotation_body(
            record["body"],
            record["audience"],
            record["operationId"],
            manifest["installation_id"],
            record["oldKeyId"],
            old_public,
            record["newKeyId"],
            new_public,
        )
        pending = manifest["pending_replacement"]
        if record["state"] == "pending":
            if (
                pending is None
                or pending["operation_id"] != record["operationId"]
                or pending["key_id"] != record["newKeyId"]
                or pending["public_key"] != record["newPublicKey"]
                or manifest["key_id"] != record["oldKeyId"]
                or record["result"] is not None
            ):
                raise ValueError
            private = Ed25519PrivateKey.from_private_bytes(
                base64.b64decode(record["candidatePrivateKey"], validate=True)
            )
            if (
                private.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )
                != new_public
            ):
                raise ValueError
        elif record["state"] == "completed":
            if (
                pending is not None
                or record["candidatePrivateKey"] is not None
                or manifest["key_id"] != record["newKeyId"]
            ):
                raise ValueError
            validate_rotation_result(
                record["result"],
                operation_id=record["operationId"],
                contributor_id=manifest["contributor_id"],
                installation_id=manifest["installation_id"],
                old_id=record["oldKeyId"],
                new_id=record["newKeyId"],
            )
        else:
            raise ValueError
    except (ValueError, TypeError, KeyError, RuntimeError) as error:
        raise IdentityUnavailable("corrupt", "Invalid private rotation journal") from error


def prepare_rotation(home: IdentityHome, audience: str):
    """Persist candidate, operation and exact proofs together before any network send."""
    from dataclasses import replace

    from .community_identity import (
        MANIFEST_FILE,
        PendingReplacement,
        _atomic_write,
        _directory,
        _load,
        _locked,
        _manifest_for,
    )

    root = _directory(home)
    with _locked(home.data_dir):
        identity = _load(root)
        if identity.recovery and identity.recovery["state"] == "pending":
            raise IdentityUnavailable("pending", "Finish the pending key recovery first")
        if identity.rotation and identity.rotation["state"] == "pending":
            if identity.rotation["audience"] != audience:
                raise IdentityUnavailable("pending", "Rotation belongs to another service")
            return identity
        state = identity.client_state or {}
        if (
            identity.last_known_key_state != "active"
            or not identity.contributor_id
            or state.get("pending")
            or state.get("rename")
        ):
            raise IdentityUnavailable("pending", "Finish other identity operations first")
        if identity.pending_replacement is not None:
            raise IdentityUnavailable("pending", "Legacy replacement requires recovery")
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        pending = PendingReplacement(
            str(uuid.uuid4()), "sha256:" + hashlib.sha256(public).hexdigest(), public, private
        )
        identity = replace(identity, pending_replacement=pending)
        manifest = _manifest_for(identity)
        manifest.pop("recovery", None)
        manifest["key_file"] = identity.key_file
        manifest["rotation"] = {
            "version": 1,
            "state": "pending",
            "audience": audience,
            "operationId": pending.operation_id,
            "oldKeyId": identity.key_id,
            "oldPublicKey": base64.b64encode(identity.public_key).decode("ascii"),
            "newKeyId": pending.key_id,
            "newPublicKey": base64.b64encode(public).decode("ascii"),
            "body": build_rotation_body(identity, audience).decode("ascii"),
            "candidatePrivateKey": base64.b64encode(
                private.private_bytes(
                    serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw,
                    serialization.NoEncryption(),
                )
            ).decode("ascii"),
            "result": None,
        }
        validate_rotation_journal(manifest)
        _atomic_write(root / MANIFEST_FILE, json.dumps(manifest, separators=(",", ":")).encode())
        return _load(root)


def activate_rotation(home: IdentityHome, operation_id: str, result: dict):
    """Publish the verified replacement with one atomic manifest pointer switch."""
    from .community_identity import (
        KEY_FILE,
        MANIFEST_FILE,
        PENDING_FILE,
        _atomic_write,
        _directory,
        _load,
        _locked,
        _manifest_for,
    )

    root = _directory(home)
    with _locked(home.data_dir):
        identity = _load(root)
        record = identity.rotation
        if record is None or record["operationId"] != operation_id:
            raise IdentityUnavailable("pending", "Rotation changed before activation")
        validate_rotation_result(
            result,
            operation_id=operation_id,
            contributor_id=identity.contributor_id,
            installation_id=identity.installation_id,
            old_id=record["oldKeyId"],
            new_id=record["newKeyId"],
        )
        if record["state"] == "completed":
            if result != record["result"]:
                raise IdentityUnavailable("corrupt", "Conflicting rotation outcome")
            return identity
        target = PENDING_FILE if identity.key_file == KEY_FILE else KEY_FILE
        _atomic_write(root / target, record["candidatePrivateKey"].encode("ascii") + b"\n")
        manifest = _manifest_for(identity)
        manifest.update(
            key_id=record["newKeyId"],
            public_key=record["newPublicKey"],
            key_file=target,
            pending_replacement=None,
            last_known_key_state="active",
        )
        manifest["rotation"] = copy.deepcopy(record) | {
            "state": "completed",
            "result": result,
            "candidatePrivateKey": None,
        }
        if manifest.get("client_state", {}).get("profile"):
            manifest["client_state"] = copy.deepcopy(manifest["client_state"])
            manifest["client_state"]["profile"]["keyId"] = record["newKeyId"]
        validate_rotation_journal(manifest)
        _atomic_write(root / MANIFEST_FILE, json.dumps(manifest, separators=(",", ":")).encode())
        return _load(root)
