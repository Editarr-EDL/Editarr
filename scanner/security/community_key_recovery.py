"""Private, durable intent for explicitly confirmed Discord-assisted key recovery."""

import base64
import copy
import hashlib
import json
import re
import secrets
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .community_identity import (
    KEY_FILE,
    MANIFEST_FILE,
    PENDING_FILE,
    _atomic_write,
    _decode_key,
    _directory,
    _load,
    _locked,
    _manifest_for,
)
from .private_identity_files import IdentityHome, IdentityUnavailable


def validate_result(result, record, contributor_id):
    """Accept only the exact contributor, installation, candidate and saved operation."""
    expected = {
        "version": 1,
        "kind": "key_recovery",
        "operationId": record["operationId"],
        "contributorId": contributor_id,
        "installationId": record["installationId"],
        "oldKeyId": record["oldKeyId"],
        "newKeyId": record["newKeyId"],
    }
    if (
        not isinstance(result, dict)
        or set(result) != set(expected) | {"committedAt", "stateId"}
        or any(result[name] != value for name, value in expected.items())
        or type(result["version"]) is not int
        or type(result["committedAt"]) is not int
        or result["committedAt"] < 0
        or not isinstance(result["stateId"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", result["stateId"])
    ):
        raise IdentityUnavailable("corrupt", "Invalid recovery outcome")


def validate_recovery_journal(manifest):
    record = manifest.get("recovery")
    if record is None:
        return
    try:
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "version",
                "state",
                "audience",
                "operationId",
                "installationId",
                "oldKeyId",
                "localOldKeyId",
                "newKeyId",
                "publicKey",
                "candidatePrivateKey",
                "secret",
                "stateId",
                "body",
                "result",
            }
            or type(record["version"]) is not int
            or record["version"] != 1
            or str(uuid.UUID(record["operationId"])) != record["operationId"]
            or record["installationId"] != manifest["installation_id"]
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", record["oldKeyId"])
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", record["localOldKeyId"])
        ):
            raise ValueError
        from scanner.integrations.community.transport import ServiceEndpoint

        ServiceEndpoint(manifest["service_url"], record["audience"])
        public = _decode_key(record["publicKey"])
        if "sha256:" + hashlib.sha256(public).hexdigest() != record["newKeyId"]:
            raise ValueError
        if record["stateId"] is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", record["stateId"]):
                raise ValueError
            payload = json.loads(record["body"])
            if payload != {
                "version": 1,
                "stateId": record["stateId"],
                "operationId": record["operationId"],
                "installationId": record["installationId"],
                "oldKeyId": record["oldKeyId"],
                "confirmationSecret": record["secret"],
            }:
                raise ValueError
        elif record["body"] is not None:
            raise ValueError
        if not re.fullmatch(r"[0-9a-f]{64}", record["secret"]):
            raise ValueError
        if record["state"] == "pending":
            private = Ed25519PrivateKey.from_private_bytes(
                _decode_key(record["candidatePrivateKey"])
            )
            if (
                private.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )
                != public
                or manifest["key_id"] != record["localOldKeyId"]
                or record["result"] is not None
            ):
                raise ValueError
        elif record["state"] == "completed":
            if (
                record["candidatePrivateKey"] is not None
                or manifest["key_id"] != record["newKeyId"]
            ):
                raise ValueError
            validate_result(record["result"], record, manifest["contributor_id"])
        else:
            raise ValueError
    except (ValueError, TypeError, KeyError, RuntimeError) as error:
        raise IdentityUnavailable("corrupt", "Invalid private recovery journal") from error


def recovery_source(home: IdentityHome):
    """Read retained public identity even when the old private-key file is missing."""
    root = _directory(home)
    with _locked(home.data_dir):
        return _load(root, allow_missing_private=True)


def prepare_recovery(home: IdentityHome, audience: str, old_key_id: str):
    """Create one candidate only after the authenticated user's explicit recovery action."""
    root = _directory(home)
    with _locked(home.data_dir):
        identity = _load(root, allow_missing_private=True)
        if identity.recovery and identity.recovery["state"] == "pending":
            if (
                identity.recovery["oldKeyId"] != old_key_id
                or identity.recovery["audience"] != audience
            ):
                raise IdentityUnavailable("pending", "A different recovery is pending")
            return identity
        if (
            not identity.contributor_id
            or not identity.service_url
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", old_key_id)
        ):
            raise IdentityUnavailable(
                "incomplete", "Restore retained installation metadata before recovery"
            )
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        record = {
            "version": 1,
            "state": "pending",
            "audience": audience,
            "operationId": str(uuid.uuid4()),
            "installationId": identity.installation_id,
            "oldKeyId": old_key_id,
            "localOldKeyId": identity.key_id,
            "newKeyId": "sha256:" + hashlib.sha256(public).hexdigest(),
            "publicKey": base64.b64encode(public).decode("ascii"),
            "candidatePrivateKey": base64.b64encode(
                private.private_bytes(
                    serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw,
                    serialization.NoEncryption(),
                )
            ).decode("ascii"),
            "secret": secrets.token_hex(32),
            "stateId": None,
            "body": None,
            "result": None,
        }
        manifest = _manifest_for(identity) | {"key_file": identity.key_file, "recovery": record}
        validate_recovery_journal(manifest)
        _atomic_write(root / MANIFEST_FILE, json.dumps(manifest, separators=(",", ":")).encode())
        return _load(root, allow_missing_private=True)


def bind_recovery(home: IdentityHome, state_id: str):
    """Persist the exact signed body before sending; retain it on every uncertain outcome."""
    root = _directory(home)
    with _locked(home.data_dir):
        identity = _load(root, allow_missing_private=True)
        record = copy.deepcopy(identity.recovery)
        if (
            not record
            or record["state"] != "pending"
            or not re.fullmatch(r"[0-9a-f]{64}", state_id)
        ):
            raise IdentityUnavailable("pending", "No matching recovery is pending")
        record["stateId"] = state_id
        record["body"] = json.dumps(
            {
                "version": 1,
                "stateId": state_id,
                "operationId": record["operationId"],
                "installationId": record["installationId"],
                "oldKeyId": record["oldKeyId"],
                "confirmationSecret": record["secret"],
            },
            separators=(",", ":"),
        )
        manifest = _manifest_for(identity) | {"recovery": record}
        validate_recovery_journal(manifest)
        _atomic_write(root / MANIFEST_FILE, json.dumps(manifest, separators=(",", ":")).encode())
        return _load(root, allow_missing_private=True)


def activate_recovery(home: IdentityHome, result: dict):
    """Publish the verified candidate with an atomic pointer; never recreate the contributor."""
    root = _directory(home)
    with _locked(home.data_dir):
        identity = _load(root, allow_missing_private=True)
        record = identity.recovery
        if not record:
            raise IdentityUnavailable("pending", "No recovery is pending")
        validate_result(result, record, identity.contributor_id)
        if record["state"] == "completed":
            if result != record["result"]:
                raise IdentityUnavailable("corrupt", "Conflicting recovery result")
            return identity
        target = PENDING_FILE if identity.key_file == KEY_FILE else KEY_FILE
        _atomic_write(root / target, record["candidatePrivateKey"].encode("ascii") + b"\n")
        manifest = _manifest_for(identity)
        manifest.update(
            key_id=record["newKeyId"],
            public_key=record["publicKey"],
            key_file=target,
            pending_replacement=None,
            last_known_key_state="active",
        )
        manifest.pop("rotation", None)
        manifest["recovery"] = copy.deepcopy(record) | {
            "state": "completed",
            "candidatePrivateKey": None,
            "result": result,
        }
        if manifest.get("client_state"):
            manifest["client_state"] = copy.deepcopy(manifest["client_state"])
            manifest["client_state"].update(pending=None, rename=None)
            if manifest["client_state"].get("profile"):
                manifest["client_state"]["profile"]["keyId"] = record["newKeyId"]
        validate_recovery_journal(manifest)
        _atomic_write(root / MANIFEST_FILE, json.dumps(manifest, separators=(",", ":")).encode())
        return _load(root)
