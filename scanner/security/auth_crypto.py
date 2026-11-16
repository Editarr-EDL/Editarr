"""CSRF-token encryption key and AES-256-GCM envelope handling."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from scanner.runtime.maintenance import DatabaseMaintenance

_KEY_ENVIRONMENT_NAME = "EDITARR_AUTH_CSRF_ENCRYPTION_KEY"
_KEY_FILENAME = "auth-csrf.key"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_TOKEN_BYTES = 32
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_ENVELOPE_PATTERN = re.compile(
    r"^v1\.([0-9a-f]{16})\.([A-Za-z0-9_-]{16})\.([A-Za-z0-9_-]{64})$"
)
_ASSOCIATED_DATA_PREFIX = b"editarr-csrf-wrap-v1\0"


class CsrfKeyError(RuntimeError):
    """Raised for an unsafe or malformed CSRF encryption key configuration."""


class CsrfEnvelopeError(ValueError):
    """Raised for an invalid, mismatched, or unauthentic CSRF envelope."""


@dataclass(frozen=True, slots=True)
class CsrfEncryptionKey:
    """The in-memory AES key; its value intentionally never appears in ``repr``."""

    _value: bytes
    key_id: str

    @classmethod
    def from_bytes(cls, value: bytes) -> CsrfEncryptionKey:
        if len(value) != _KEY_BYTES:
            raise CsrfKeyError("CSRF encryption key must contain exactly 32 bytes")
        return cls(value, hashlib.sha256(value).hexdigest()[:16])


def load_csrf_encryption_key(
    data_dir: Path, *, environment: dict[str, str] | None = None
) -> CsrfEncryptionKey:
    """Load the env key or safely create/read the service-owned persistent key."""

    environment = os.environ if environment is None else environment
    environment_value = environment.get(_KEY_ENVIRONMENT_NAME)
    if environment_value is not None:
        return CsrfEncryptionKey.from_bytes(_decode_canonical(environment_value, _KEY_BYTES))
    with DatabaseMaintenance(data_dir).worker_lease():
        return CsrfEncryptionKey.from_bytes(
            _load_or_create_key_file(Path(data_dir) / "secrets" / _KEY_FILENAME)
        )


def encrypt_csrf_token(token: str, session_token_digest: str, key: CsrfEncryptionKey) -> str:
    """Return the specified versioned envelope bound to one session digest."""

    plaintext = _decode_canonical(token, _TOKEN_BYTES)
    nonce = os.urandom(_NONCE_BYTES)
    encrypted = AESGCM(key._value).encrypt(nonce, plaintext, _associated_data(session_token_digest))
    return f"v1.{key.key_id}.{_encode_canonical(nonce)}.{_encode_canonical(encrypted)}"


def decrypt_csrf_token(envelope: str, session_token_digest: str, key: CsrfEncryptionKey) -> str:
    """Authenticate/decrypt an envelope or provide one deliberately generic failure."""

    match = _ENVELOPE_PATTERN.fullmatch(envelope) if isinstance(envelope, str) else None
    if match is None or match.group(1) != key.key_id:
        raise CsrfEnvelopeError("CSRF session recovery failed")
    try:
        nonce = _decode_canonical(match.group(2), _NONCE_BYTES)
        ciphertext = _decode_canonical(match.group(3), _TOKEN_BYTES + 16)
        plaintext = AESGCM(key._value).decrypt(
            nonce, ciphertext, _associated_data(session_token_digest)
        )
    except (CsrfKeyError, InvalidTag, ValueError, TypeError) as error:
        raise CsrfEnvelopeError("CSRF session recovery failed") from error
    if len(plaintext) != _TOKEN_BYTES:
        raise CsrfEnvelopeError("CSRF session recovery failed")
    return _encode_canonical(plaintext)


def envelope_key_id(envelope: str) -> str | None:
    """Return a syntactically valid envelope's key ID without decrypting it."""

    match = _ENVELOPE_PATTERN.fullmatch(envelope) if isinstance(envelope, str) else None
    return match.group(1) if match is not None else None


def _load_or_create_key_file(path: Path) -> bytes:
    """Read or atomically create the local key file used to protect session recovery data."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_private_directory(path.parent)
    temporary_path = path.parent / f".{path.name}.{os.urandom(16).hex()}.tmp"
    try:
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except FileExistsError:
        # A cryptographically random temporary name colliding is extraordinarily
        # unlikely, but retry through the established completed-file path rather than
        # choosing a predictable alternate filename.
        return _read_private_key_file(path)
    except OSError as error:
        raise CsrfKeyError("Unable to create CSRF encryption key file") from error
    try:
        try:
            value = os.urandom(_KEY_BYTES)
            encoded = _encode_canonical(value)
            os.write(descriptor, (encoded + "\n").encode("ascii"))
            os.fsync(descriptor)
        except OSError as error:
            raise CsrfKeyError("Unable to create CSRF encryption key file") from error
        finally:
            os.close(descriptor)
        try:
            # ``link`` is an atomic create-if-absent publication step. Unlike opening
            # the final path with O_EXCL, it never leaves a partially written final key
            # for a concurrent loser to read.
            os.link(temporary_path, path)
        except FileExistsError:
            return _read_private_key_file(path)
        except OSError as error:
            raise CsrfKeyError("Unable to create CSRF encryption key file") from error
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # The key was never published from a temporary file after a failed write.
            # Do not mask that original security-relevant failure with cleanup noise.
            pass
    # Re-open with the same safety checks so creation and reading have one contract.
    return _read_private_key_file(path)


def _read_private_key_file(path: Path, *, platform_name: str | None = None) -> bytes:
    """Read a regular key file, enforcing POSIX mode bits only where meaningful."""

    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise CsrfKeyError("Unable to read CSRF encryption key file") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise CsrfKeyError("CSRF encryption key file must be a regular file")
    if _uses_posix_permission_bits(platform_name) and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise CsrfKeyError("CSRF encryption key file permissions must be owner-only")
    try:
        with path.open("rb") as key_file:
            contents = key_file.read()
    except OSError as error:
        raise CsrfKeyError("Unable to read CSRF encryption key file") from error
    if contents.endswith(b"\r\n"):
        encoded_bytes = contents[:-2]
    elif contents.endswith(b"\n"):
        encoded_bytes = contents[:-1]
    else:
        raise CsrfKeyError("CSRF encryption key file is malformed")
    # Accept canonical CRLF from the earlier Windows text-mode writer so an
    # existing v1 key remains usable.  No other newline or carriage return is
    # admitted, including embedded/newline-injected key material.
    if b"\n" in encoded_bytes or b"\r" in encoded_bytes:
        raise CsrfKeyError("CSRF encryption key file is malformed")
    try:
        encoded = encoded_bytes.decode("ascii")
    except UnicodeDecodeError as error:
        raise CsrfKeyError("CSRF encryption key file is malformed") from error
    return _decode_canonical(encoded, _KEY_BYTES)


def _require_private_directory(path: Path, *, platform_name: str | None = None) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise CsrfKeyError("Unable to inspect CSRF key directory") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise CsrfKeyError("CSRF key directory must be a real directory")
    if _uses_posix_permission_bits(platform_name) and stat.S_IMODE(metadata.st_mode) & 0o077:
        try:
            path.chmod(0o700)
        except OSError as error:
            raise CsrfKeyError("CSRF key directory permissions must be owner-only") from error


def _uses_posix_permission_bits(platform_name: str | None = None) -> bool:
    """Whether mode bits establish the owner-only access boundary on this host.

    Windows `st_mode` permission bits are a compatibility projection, not proof of
    the file's DACL. Regular-file and symlink checks remain mandatory there; the
    per-user data root supplies the Windows ACL boundary.
    """

    return (os.name if platform_name is None else platform_name) == "posix"


def _associated_data(session_token_digest: str) -> bytes:
    if not isinstance(session_token_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", session_token_digest
    ):
        raise CsrfEnvelopeError("CSRF session recovery failed")
    return _ASSOCIATED_DATA_PREFIX + bytes.fromhex(session_token_digest)


def _decode_canonical(value: str, expected_bytes: int) -> bytes:
    """Decode a canonical encoded token and reject alternate encodings."""
    if not isinstance(value, str):
        raise CsrfKeyError("CSRF encryption key is malformed")
    expected_length = (expected_bytes * 8 + 5) // 6
    if len(value) != expected_length or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise CsrfKeyError("CSRF encryption key is malformed")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, UnicodeEncodeError) as error:
        raise CsrfKeyError("CSRF encryption key is malformed") from error
    if len(raw) != expected_bytes or _encode_canonical(raw) != value:
        raise CsrfKeyError("CSRF encryption key is malformed")
    return raw


def _encode_canonical(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
