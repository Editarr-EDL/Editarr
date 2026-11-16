"""Wrap each stored API key so its holder can reveal it without plaintext at rest.

The wrap key is a private 32-byte file beside the CSRF key, created on first use
with the same file contract. A wrapped value names its wrap key and is bound to
the key's digest, so it cannot be moved to another row. Backups carry the file
with the local accounts.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from scanner.runtime.maintenance import DatabaseMaintenance

from .auth_crypto import (
    CsrfKeyError,
    _decode_canonical,
    _encode_canonical,
    _load_or_create_key_file,
)

WRAP_KEY_FILENAME = "api-key-wrap.key"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_ENVELOPE = re.compile(r"^v1\.([0-9a-f]{16})\.([A-Za-z0-9_-]{16})\.([A-Za-z0-9_-]{64})$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ASSOCIATED_DATA_PREFIX = b"editarr-api-key-wrap-v1\0"


class ApiKeyEnvelopeError(ValueError):
    """The wrapped copy is malformed, under another wrap key, or not this key's."""


@dataclass(frozen=True, slots=True)
class ApiKeyWrapKey:
    """The in-memory AES key; its value never appears in ``repr``."""

    _value: bytes = field(repr=False)
    key_id: str


def load_api_key_wrap_key(data_dir: Path) -> ApiKeyWrapKey:
    """Read or create `secrets/api-key-wrap.key`; raises `CsrfKeyError` for an unsafe file."""

    with DatabaseMaintenance(data_dir).worker_lease():
        value = _load_or_create_key_file(Path(data_dir) / "secrets" / WRAP_KEY_FILENAME)
    if len(value) != _KEY_BYTES:
        raise CsrfKeyError("API key wrap key must contain exactly 32 bytes")
    return ApiKeyWrapKey(value, hashlib.sha256(value).hexdigest()[:16])


def seal_api_key(key: str, digest: str, wrap_key: ApiKeyWrapKey) -> str:
    """Wrap a 64-hex-character key under `wrap_key`, bound to its digest."""

    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(wrap_key._value).encrypt(nonce, bytes.fromhex(key), _associated(digest))
    return f"v1.{wrap_key.key_id}.{_encode_canonical(nonce)}.{_encode_canonical(sealed)}"


def open_api_key(envelope: str, digest: str, wrap_key: ApiKeyWrapKey) -> str:
    """Return the key a wrapped copy holds, or raise one generic `ApiKeyEnvelopeError`."""

    match = _ENVELOPE.fullmatch(envelope)
    if match is None or match.group(1) != wrap_key.key_id:
        raise ApiKeyEnvelopeError("API key cannot be recovered")
    try:
        nonce = _decode_canonical(match.group(2), _NONCE_BYTES)
        sealed = _decode_canonical(match.group(3), _KEY_BYTES + 16)
        key = AESGCM(wrap_key._value).decrypt(nonce, sealed, _associated(digest)).hex()
    except (CsrfKeyError, InvalidTag, ValueError) as error:
        raise ApiKeyEnvelopeError("API key cannot be recovered") from error
    if hashlib.sha256(key.encode()).hexdigest() != digest:
        raise ApiKeyEnvelopeError("API key cannot be recovered")
    return key


def _associated(digest: str) -> bytes:
    if _DIGEST.fullmatch(digest) is None:
        raise ApiKeyEnvelopeError("API key cannot be recovered")
    return _ASSOCIATED_DATA_PREFIX + bytes.fromhex(digest)
