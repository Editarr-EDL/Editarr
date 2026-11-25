"""Validate the service receipt-key history that master-set snapshot receipts verify against."""

import base64
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class ReceiptError(ValueError):
    """A receipt key or its independently retrieved service key history is invalid."""


class ReceiptKey(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    key_id: str = Field(alias="keyId", pattern=r"^sha256:[a-f0-9]{64}$")
    public_key: str = Field(alias="publicKey", max_length=44)
    status: Literal["active", "retired", "revoked"]


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
