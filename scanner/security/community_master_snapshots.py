"""Verify service-signed master snapshots and their download receipts (§7.3)."""

import base64
import binascii
import hashlib
from dataclasses import dataclass
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scanner.formats.community.master_snapshot import (
    DEFAULT_MAX_FILTERS,
    MAX_SNAPSHOT_BYTES,
    MasterSnapshot,
    MasterSnapshotError,
    parse_master_snapshot,
)
from scanner.formats.community.strict_json import MAX_SAFE_INTEGER

from .community_download_receipts import ReceiptKey, _framed, parse_receipt_keys, public_bytes
from .community_signatures import _url_components

SNAPSHOT_DOMAIN = "editarr-community-master-snapshot-v1"
RECEIPT_DOMAIN = "editarr-community-master-receipt-v1"
RECEIPT_IDENTITY_DOMAIN = "editarr-community-master-receipt-identity-v1"
_TOKEN = r"^[A-Za-z0-9_.-]{1,128}$"
_KEY_ID = r"^sha256:[a-f0-9]{64}$"
_HEX64 = r"^[a-f0-9]{64}$"


class MasterVerificationError(ValueError):
    """A master response failed verification and must be rejected as a whole."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SnapshotSignature(_Strict):
    schema_version: Literal["community-master-signature-1"] = Field(alias="schemaVersion")
    service: str = Field(max_length=256)
    service_key_id: str = Field(alias="serviceKeyId", pattern=_KEY_ID)
    version_id: str = Field(alias="versionId", pattern=_TOKEN)
    revision: int = Field(ge=1, le=MAX_SAFE_INTEGER)
    snapshot_digest: str = Field(alias="snapshotDigest", pattern=_HEX64)
    issued_at: int = Field(alias="issuedAt", ge=0, le=MAX_SAFE_INTEGER)
    signature: str = Field(max_length=88)


class MasterReceiptFields(_Strict):
    schema_version: Literal["community-master-receipt-1"] = Field(alias="schemaVersion")
    service: str = Field(max_length=256)
    service_key_id: str = Field(alias="serviceKeyId", pattern=_KEY_ID)
    contributor_id: str = Field(alias="contributorId", pattern=_TOKEN)
    version_id: str = Field(alias="versionId", pattern=_TOKEN)
    revision: int = Field(ge=1, le=MAX_SAFE_INTEGER)
    snapshot_digest: str = Field(alias="snapshotDigest", pattern=_HEX64)
    receipt_id: str = Field(alias="receiptId", pattern=_HEX64)
    issued_at: int = Field(alias="issuedAt", ge=0, le=MAX_SAFE_INTEGER)


class MasterReceipt(_Strict):
    receipt: MasterReceiptFields
    signature: str = Field(max_length=88)


class _Changed(_Strict):
    version: Literal[1]
    changed: Literal[True]
    snapshot: str = Field(max_length=4 * ((MAX_SNAPSHOT_BYTES + 2) // 3))
    snapshot_signature: SnapshotSignature = Field(alias="snapshotSignature")
    receipt: MasterReceipt


class _Unchanged(_Strict):
    version: Literal[1]
    changed: Literal[False]
    version_id: str = Field(alias="versionId", pattern=_TOKEN)
    revision: int = Field(ge=1, le=MAX_SAFE_INTEGER)


@dataclass(frozen=True)
class VerifiedMaster:
    """A verified newer revision: exact snapshot bytes, parsed snapshot and proofs."""

    snapshot_bytes: bytes
    snapshot: MasterSnapshot
    snapshot_signature: SnapshotSignature
    receipt: MasterReceipt


@dataclass(frozen=True)
class MasterUnchanged:
    version_id: str
    revision: int


def snapshot_signing_bytes(signature: SnapshotSignature) -> bytes:
    """Return the framed snapshot-signature message with its u64 `issuedAt` suffix."""
    return _framed(
        SNAPSHOT_DOMAIN,
        (
            signature.schema_version,
            signature.service,
            signature.service_key_id,
            signature.version_id,
            str(signature.revision),
            signature.snapshot_digest,
        ),
    ) + signature.issued_at.to_bytes(8, "big")


def master_receipt_identity(
    service: str, contributor_id: str, version_id: str, revision: int, snapshot_digest: str
) -> str:
    """Return the stable receipt ID, which excludes `issuedAt` and the service key."""
    fields = (service, contributor_id, version_id, str(revision), snapshot_digest)
    return hashlib.sha256(_framed(RECEIPT_IDENTITY_DOMAIN, fields)).hexdigest()


def master_receipt_signing_bytes(receipt: MasterReceiptFields) -> bytes:
    """Return the framed receipt message with its u64 `issuedAt` suffix."""
    return _framed(
        RECEIPT_DOMAIN,
        (
            receipt.schema_version,
            receipt.service,
            receipt.service_key_id,
            receipt.contributor_id,
            receipt.version_id,
            str(receipt.revision),
            receipt.snapshot_digest,
            receipt.receipt_id,
        ),
    ) + receipt.issued_at.to_bytes(8, "big")


def sign_master_message(private_key: Ed25519PrivateKey, message: bytes) -> str:
    """Sign a framed master message (used by tests and fixtures acting as the service)."""
    return base64.b64encode(private_key.sign(message)).decode("ascii")


def _canonical_service(service: str) -> bool:
    try:
        _, authority, path, query = _url_components(service)
    except ValueError:
        return False
    return service == f"https://{authority}" and path == "/" and query == "?"


def _verify(keys: tuple[ReceiptKey, ...], key_id: str, signature: str, message: bytes) -> None:
    key = next((key for key in keys if key.key_id == key_id), None)
    if key is None or key.status not in {"active", "retired"}:
        raise ValueError("Service key is unknown or revoked")
    Ed25519PublicKey.from_public_bytes(public_bytes(key.public_key, 32)).verify(
        public_bytes(signature, 64), message
    )


def _decode_snapshot(encoded: str) -> bytes:
    decoded = base64.b64decode(encoded, validate=True)
    if base64.b64encode(decoded).decode("ascii") != encoded:
        raise ValueError("Noncanonical snapshot base64")
    return decoded


def verify_master_response(
    value: object,
    *,
    service: str,
    contributor_id: str,
    version_id: str,
    known_revision: int | None,
    trusted_keys: tuple[ReceiptKey, ...],
    max_filters: int = DEFAULT_MAX_FILTERS,
) -> VerifiedMaster | MasterUnchanged:
    """Verify a `GET /v1/versions/{id}/master` body against the request and pinned keys.

    Follows the §7.3 client steps; any failure rejects the whole response.
    """
    try:
        if not _canonical_service(service):
            raise ValueError("Expected a canonical service origin")
        if isinstance(value, dict) and value.get("changed") is False:
            unchanged = _Unchanged.model_validate(value, strict=True)
            if (
                unchanged.version_id != version_id
                or known_revision is None
                or unchanged.revision > known_revision
            ):
                raise ValueError("Unchanged response does not match the request")
            return MasterUnchanged(unchanged.version_id, unchanged.revision)
        response = _Changed.model_validate(value, strict=True)
        keys = parse_receipt_keys([key.model_dump(by_alias=True) for key in trusted_keys])
        signed = response.snapshot_signature
        snapshot_bytes = _decode_snapshot(response.snapshot)
        if hashlib.sha256(snapshot_bytes).hexdigest() != signed.snapshot_digest:
            raise ValueError("Snapshot digest mismatch")
        if (signed.service, signed.version_id) != (service, version_id) or (
            known_revision is not None and signed.revision <= known_revision
        ):
            raise ValueError("Snapshot signature does not match the request")
        _verify(keys, signed.service_key_id, signed.signature, snapshot_signing_bytes(signed))
        snapshot = parse_master_snapshot(snapshot_bytes, max_filters=max_filters)
        if (snapshot.service, snapshot.version_id, snapshot.revision) != (
            signed.service,
            signed.version_id,
            signed.revision,
        ):
            raise ValueError("Snapshot does not match its signature")
        receipt = response.receipt.receipt
        if (
            receipt.service,
            receipt.service_key_id,
            receipt.contributor_id,
            receipt.version_id,
            receipt.revision,
            receipt.snapshot_digest,
        ) != (
            service,
            signed.service_key_id,
            contributor_id,
            version_id,
            signed.revision,
            signed.snapshot_digest,
        ) or receipt.receipt_id != master_receipt_identity(
            service, contributor_id, version_id, signed.revision, signed.snapshot_digest
        ):
            raise ValueError("Receipt binding mismatch")
        _verify(
            keys,
            receipt.service_key_id,
            response.receipt.signature,
            master_receipt_signing_bytes(receipt),
        )
    except (
        ValueError,
        ValidationError,
        InvalidSignature,
        MasterSnapshotError,
        binascii.Error,
    ) as exc:
        raise MasterVerificationError("invalid_master_response") from exc
    return VerifiedMaster(snapshot_bytes, snapshot, signed, response.receipt)
