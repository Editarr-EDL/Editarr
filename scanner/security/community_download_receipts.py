"""Verify contributor-bound download receipts against service-origin key history."""

import base64
import hashlib
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .community_signatures import _url_components


class ReceiptError(ValueError):
    """A receipt or its independently retrieved service key history is invalid."""


class ReceiptKey(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    key_id: str = Field(alias="keyId", pattern=r"^sha256:[a-f0-9]{64}$")
    public_key: str = Field(alias="publicKey", max_length=44)
    status: Literal["active", "retired", "revoked"]


class ReceiptFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["community-download-receipt-1"] = Field(alias="schemaVersion")
    service: str = Field(max_length=256)
    service_key_id: str = Field(alias="serviceKeyId", pattern=r"^sha256:[a-f0-9]{64}$")
    contributor_id: str = Field(alias="contributorId", pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    revision_id: str = Field(alias="revisionId", pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    artifact_digest: str = Field(alias="artifactDigest", pattern=r"^[a-f0-9]{64}$")
    receipt_id: str = Field(alias="receiptId", pattern=r"^[a-f0-9]{64}$")
    issued_at: int = Field(alias="issuedAt", ge=0, le=(1 << 53) - 1)


class DownloadReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    receipt: ReceiptFields
    signature: str = Field(max_length=88)


def public_bytes(value: str, length: int) -> bytes:
    """Decode fixed-size canonical padded base64 without accepting alternate encodings."""
    try:
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) != length or base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError
        return decoded
    except (ValueError, UnicodeError) as exc:
        raise ReceiptError("invalid_download_receipt") from exc


def parse_receipt_keys(value: object) -> tuple[ReceiptKey, ...]:
    """Validate the bounded key set retrieved separately from the configured origin."""
    try:
        keys = TypeAdapter(list[ReceiptKey]).validate_python(value, strict=True)
        if not 1 <= len(keys) <= 16 or len({key.key_id for key in keys}) != len(keys):
            raise ValueError
        for key in keys:
            raw = public_bytes(key.public_key, 32)
            if key.key_id != "sha256:" + hashlib.sha256(raw).hexdigest():
                raise ValueError
        return tuple(keys)
    except ValueError as exc:
        raise ReceiptError("invalid_receipt_keys") from exc


def _framed(domain: str, fields: tuple[str, ...]) -> bytes:
    encoded = tuple(field.encode("utf-8") for field in fields)
    return (
        domain.encode("ascii")
        + b"\0"
        + b"".join(len(field).to_bytes(4, "big") + field for field in encoded)
    )


def receipt_identity(service: str, contributor_id: str, revision_id: str, digest: str) -> str:
    """Return the stable receipt identity, excluding issue time and service signing key."""
    return hashlib.sha256(
        _framed(
            "editarr-community-download-identity-v1", (service, contributor_id, revision_id, digest)
        )
    ).hexdigest()


def receipt_signing_bytes(receipt: ReceiptFields) -> bytes:
    """Encode CSS-42's seven UTF-8 fields and unsigned Unix seconds exactly."""
    return _framed(
        "editarr-community-download-receipt-v1",
        (
            receipt.schema_version,
            receipt.service,
            receipt.service_key_id,
            receipt.contributor_id,
            receipt.revision_id,
            receipt.artifact_digest,
            receipt.receipt_id,
        ),
    ) + receipt.issued_at.to_bytes(8, "big")


def verify_download_receipt(
    value: object,
    *,
    service: str,
    contributor_id: str,
    revision_id: str,
    artifact_digest: str,
    trusted_keys: tuple[ReceiptKey, ...],
) -> DownloadReceipt:
    """Verify bindings and Ed25519 attribution; receipts grant no current API authority."""
    try:
        envelope = DownloadReceipt.model_validate(value, strict=True)
        receipt = envelope.receipt
        _, authority, path, query = _url_components(receipt.service)
        if receipt.service != f"https://{authority}" or path != "/" or query != "?":
            raise ValueError
        if (
            receipt.service,
            receipt.contributor_id,
            receipt.revision_id,
            receipt.artifact_digest,
        ) != (
            service,
            contributor_id,
            revision_id,
            artifact_digest,
        ) or receipt.receipt_id != receipt_identity(
            service, contributor_id, revision_id, artifact_digest
        ):
            raise ValueError
        keys = parse_receipt_keys([key.model_dump(by_alias=True) for key in trusted_keys])
        key = next((key for key in keys if key.key_id == receipt.service_key_id), None)
        if key is None or key.status == "revoked":
            raise ValueError
        Ed25519PublicKey.from_public_bytes(public_bytes(key.public_key, 32)).verify(
            public_bytes(envelope.signature, 64), receipt_signing_bytes(receipt)
        )
        return envelope
    except (ValueError, InvalidSignature) as exc:
        raise ReceiptError("invalid_download_receipt") from exc
