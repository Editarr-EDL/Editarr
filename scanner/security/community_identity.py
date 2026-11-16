"""Portable, private community signing identity, stored in one account's home."""

import base64
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scanner.runtime.maintenance import DatabaseMaintenance

from .private_identity_files import (
    DIRECTORY,
    IdentityHome,
    IdentityUnavailable,
    homes_directory,
)
from .private_identity_files import locked as _file_locked
from .private_identity_files import (
    private_directory as _directory,
)
from .private_identity_files import (
    read_file as _read_file,
)

KEY_FILE = "current.key"
PENDING_FILE = "pending.key"
MANIFEST_FILE = "identity.json"
VERSION = "community-identity-1"


@contextmanager
def _locked(data_dir: Path):
    """Serialize every identity change in a data directory, inside the worker lease."""
    with DatabaseMaintenance(Path(data_dir)).worker_lease():
        with _file_locked(homes_directory(data_dir)):
            yield


def holds_identity(root: Path) -> bool:
    """Whether a directory holds any identity file, even an incomplete or unsafe one."""
    return any(
        (root / name).exists() or (root / name).is_symlink()
        for name in (MANIFEST_FILE, KEY_FILE, PENDING_FILE)
    )


@dataclass(frozen=True)
class PendingReplacement:
    operation_id: str
    key_id: str
    public_key: bytes
    private_key: Ed25519PrivateKey = field(repr=False)


@dataclass(frozen=True)
class InstallationIdentity:
    installation_id: str
    key_id: str
    public_key: bytes
    contributor_id: str | None
    service_url: str | None
    last_known_key_state: str
    private_key: Ed25519PrivateKey | None = field(repr=False)
    pending_replacement: PendingReplacement | None = None
    client_state: dict | None = field(default=None, repr=False)
    key_file: str = KEY_FILE
    rotation: dict | None = field(default=None, repr=False)
    recovery: dict | None = field(default=None, repr=False)


@dataclass(frozen=True)
class IdentityBackupSnapshot:
    installation_id: str
    key_id: str
    files: tuple[tuple[str, bytes], ...] = field(repr=False)
    identity: InstallationIdentity = field(repr=False, compare=False)


def read_identity_backup_snapshot(data_dir: Path, root: Path) -> IdentityBackupSnapshot:
    """Copy validated identity files from `root` under the identity lock.

    Members use the incoming directory's names, so any home restores into it.
    """
    with _locked(data_dir):
        identity = _load(root, allow_missing_private=True)
        if identity.private_key is None and not (
            identity.recovery and identity.recovery["state"] == "pending"
        ):
            raise IdentityUnavailable("incomplete", "Community signing key needs recovery")
        names = (
            (MANIFEST_FILE,) if identity.private_key is None else (MANIFEST_FILE, identity.key_file)
        )
        files = [(f"{DIRECTORY}/{name}", _read_file(root / name, 16_384)) for name in names]
        if identity.pending_replacement is not None:
            pending_name = PENDING_FILE if identity.key_file == KEY_FILE else KEY_FILE
            raw = identity.pending_replacement.private_key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            files.append((f"{DIRECTORY}/{pending_name}", base64.b64encode(raw) + b"\n"))
        return IdentityBackupSnapshot(
            identity.installation_id, identity.key_id, tuple(files), identity
        )


def _atomic_write(path: Path, contents: bytes) -> None:
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as error:
        raise IdentityUnavailable("unavailable", "Unable to publish community identity") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _decode_key(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise IdentityUnavailable("corrupt", "Malformed community signing key") from error
    if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != value:
        raise IdentityUnavailable("corrupt", "Malformed community signing key")
    return raw


def _pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in items:
        if name in result:
            raise IdentityUnavailable("corrupt", "Duplicate community identity field")
        result[name] = value
    return result


def _validate_registration(contributor_id: object, service_url: object, state: object) -> None:
    if contributor_id is None and service_url is None and state == "unenrolled":
        return
    if (
        not isinstance(contributor_id, str)
        or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", contributor_id)
        or not isinstance(service_url, str)
        or len(service_url) > 256
        or not re.fullmatch(r"[\x21-\x7e]+", service_url)
        or "\\" in service_url
        or re.search(r"%(?![0-9A-Fa-f]{2})", service_url)
        or not isinstance(state, str)
        or state not in {"active", "retired", "revoked", "compromised"}
    ):
        raise IdentityUnavailable("corrupt", "Invalid community registration state")
    try:
        target = urlsplit(service_url)
        if (
            not service_url.startswith("https://")
            or not target.hostname
            or target.username
            or target.password
            or target.query
            or target.fragment
            or target.netloc != target.netloc.lower()
            or "#" in service_url
            or "?" in service_url
            or target.geturl() != service_url
        ):
            raise ValueError("Invalid community service URL")
        _ = target.port
    except ValueError as error:
        raise IdentityUnavailable("corrupt", "Invalid community service URL") from error


def _manifest_for(identity: InstallationIdentity) -> dict:
    manifest = {
        "schema_version": VERSION,
        "installation_id": identity.installation_id,
        "key_id": identity.key_id,
        "public_key": base64.b64encode(identity.public_key).decode("ascii"),
        "contributor_id": identity.contributor_id,
        "service_url": identity.service_url,
        "last_known_key_state": identity.last_known_key_state,
        "pending_replacement": (
            None
            if identity.pending_replacement is None
            else {
                "operation_id": identity.pending_replacement.operation_id,
                "key_id": identity.pending_replacement.key_id,
                "public_key": base64.b64encode(identity.pending_replacement.public_key).decode(
                    "ascii"
                ),
            }
        ),
    }
    if identity.client_state is not None:
        manifest["client_state"] = identity.client_state
    if identity.rotation is not None:
        manifest["key_file"] = identity.key_file
        manifest["rotation"] = identity.rotation
    if identity.recovery is not None:
        manifest["key_file"] = identity.key_file
        manifest["recovery"] = identity.recovery
    return manifest


def _load(root: Path, *, allow_missing_private: bool = False) -> InstallationIdentity:
    try:
        raw = _read_file(root / MANIFEST_FILE, 16_384)
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        required = {
            "schema_version",
            "installation_id",
            "key_id",
            "public_key",
            "contributor_id",
            "service_url",
            "last_known_key_state",
            "pending_replacement",
        }
        if (
            not isinstance(manifest, dict)
            or not required <= set(manifest)
            or set(manifest) - required - {"client_state", "key_file", "rotation", "recovery"}
            or ("key_file" in manifest) != ("rotation" in manifest or "recovery" in manifest)
        ):
            raise IdentityUnavailable("corrupt", "Invalid community identity manifest")
        if (
            manifest["schema_version"] != VERSION
            or str(uuid.UUID(manifest["installation_id"])) != manifest["installation_id"]
        ):
            raise IdentityUnavailable("corrupt", "Invalid community identity state")
        _validate_registration(
            manifest["contributor_id"], manifest["service_url"], manifest["last_known_key_state"]
        )
        from .community_client_state import validate_state

        validate_state(manifest.get("client_state"), manifest)
        public = _decode_key(manifest["public_key"])
        key_id = "sha256:" + hashlib.sha256(public).hexdigest()
        if manifest["key_id"] != key_id:
            raise IdentityUnavailable("corrupt", "Community identity key ID does not match")
        key_file = manifest.get("key_file", KEY_FILE)
        if key_file not in {KEY_FILE, PENDING_FILE}:
            raise IdentityUnavailable("corrupt", "Invalid active key slot")
        missing_private = not (root / key_file).exists() and not (root / key_file).is_symlink()
        if missing_private and not allow_missing_private:
            raise IdentityUnavailable("incomplete", "Community signing key needs recovery")
        private_key = None
        if not missing_private:
            private_raw = _read_file(root / key_file, 64)
            if not private_raw.endswith(b"\n"):
                raise IdentityUnavailable("corrupt", "Malformed community signing key")
            private_key = Ed25519PrivateKey.from_private_bytes(
                _decode_key(private_raw[:-1].decode("ascii"))
            )
            actual = private_key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            if actual != public:
                raise IdentityUnavailable("corrupt", "Community identity public key does not match")
        pending_record = manifest["pending_replacement"]
        pending_path = root / (PENDING_FILE if key_file == KEY_FILE else KEY_FILE)
        if pending_record is None:
            if (
                "rotation" not in manifest
                and "recovery" not in manifest
                and (pending_path.exists() or pending_path.is_symlink())
            ):
                raise IdentityUnavailable("incomplete", "Unrecorded pending community key")
            pending = None
        else:
            if not isinstance(pending_record, dict) or pending_record.keys() != {
                "operation_id",
                "key_id",
                "public_key",
            }:
                raise IdentityUnavailable("corrupt", "Invalid pending community key record")
            operation_id = pending_record["operation_id"]
            if not isinstance(operation_id, str) or not re.fullmatch(
                r"[A-Za-z0-9._~-]{1,128}", operation_id
            ):
                raise IdentityUnavailable("corrupt", "Invalid pending operation ID")
            pending_public = _decode_key(pending_record["public_key"])
            pending_id = "sha256:" + hashlib.sha256(pending_public).hexdigest()
            if pending_record["key_id"] != pending_id or pending_id == key_id:
                raise IdentityUnavailable("corrupt", "Invalid pending signing key identity")
            rotation = manifest.get("rotation")
            journal_key = (
                rotation.get("candidatePrivateKey") if isinstance(rotation, dict) else None
            )
            if journal_key is None and not pending_path.exists() and not pending_path.is_symlink():
                raise IdentityUnavailable("incomplete", "Pending community key needs recovery")
            pending_bytes = (
                (journal_key + "\n").encode("ascii")
                if journal_key is not None
                else _read_file(pending_path, 64)
            )
            if not pending_bytes.endswith(b"\n"):
                raise IdentityUnavailable("corrupt", "Malformed pending signing key")
            pending_key = Ed25519PrivateKey.from_private_bytes(
                _decode_key(pending_bytes[:-1].decode("ascii"))
            )
            if (
                pending_key.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )
                != pending_public
            ):
                raise IdentityUnavailable("corrupt", "Pending signing key does not match")
            pending = PendingReplacement(operation_id, pending_id, pending_public, pending_key)
        from .community_key_rotation import validate_rotation_journal

        validate_rotation_journal(manifest)
        from .community_key_recovery import validate_recovery_journal

        validate_recovery_journal(manifest)
    except (UnicodeError, ValueError, TypeError, KeyError, AttributeError) as error:
        if isinstance(error, IdentityUnavailable):
            raise
        raise IdentityUnavailable("corrupt", "Malformed community identity") from error
    return InstallationIdentity(
        manifest["installation_id"],
        key_id,
        public,
        manifest["contributor_id"],
        manifest["service_url"],
        manifest["last_known_key_state"],
        private_key,
        pending,
        manifest.get("client_state"),
        key_file,
        manifest.get("rotation"),
        manifest.get("recovery"),
    )


def stage_identity_replacement(home: IdentityHome, *, operation_id: str) -> InstallationIdentity:
    """Reserve one durable replacement for an explicitly initiated future rotation."""
    if not isinstance(operation_id, str) or not re.fullmatch(
        r"[A-Za-z0-9._~-]{1,128}", operation_id
    ):
        raise ValueError("Invalid replacement operation ID")
    with _locked(home.data_dir):
        root = _directory(home)
        current = _load(root)
        if current.rotation is not None or current.recovery is not None:
            raise IdentityUnavailable("pending", "Use the journaled rotation workflow")
        if current.pending_replacement is not None:
            if current.pending_replacement.operation_id == operation_id:
                return current
            raise IdentityUnavailable("pending", "A different community replacement is pending")
        if current.last_known_key_state != "active":
            raise IdentityUnavailable("unavailable", "Only an active key can stage replacement")
        pending_key = Ed25519PrivateKey.generate()
        pending_bytes = pending_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        pending_public = pending_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        _atomic_write(root / PENDING_FILE, base64.b64encode(pending_bytes) + b"\n")
        identity = InstallationIdentity(
            current.installation_id,
            current.key_id,
            current.public_key,
            current.contributor_id,
            current.service_url,
            current.last_known_key_state,
            current.private_key,
            PendingReplacement(
                operation_id,
                "sha256:" + hashlib.sha256(pending_public).hexdigest(),
                pending_public,
                pending_key,
            ),
            current.client_state,
        )
        _atomic_write(
            root / MANIFEST_FILE,
            json.dumps(_manifest_for(identity), separators=(",", ":")).encode(),
        )
        return _load(root)


def save_identity_registration(
    home: IdentityHome,
    *,
    contributor_id: str,
    service_url: str,
    last_known_key_state: str,
) -> InstallationIdentity:
    """Persist a verified enrollment result without replacing the installation key."""
    _validate_registration(contributor_id, service_url, last_known_key_state)
    with _locked(home.data_dir):
        root = _directory(home)
        current = _load(root)
        if current.contributor_id is not None and current.contributor_id != contributor_id:
            raise IdentityUnavailable("corrupt", "Community contributor cannot change silently")
        manifest = _manifest_for(current)
        manifest.update(
            contributor_id=contributor_id,
            service_url=service_url,
            last_known_key_state=last_known_key_state,
        )
        _atomic_write(root / MANIFEST_FILE, json.dumps(manifest, separators=(",", ":")).encode())
        return _load(root)


def load_or_create_installation_identity(home: IdentityHome) -> InstallationIdentity:
    """Create once under the identity lock; incomplete identities require explicit recovery.

    Creation is refused while the incoming directory holds identity files, because
    they belong to an existing identity that must be moved into its home first.
    """
    with _locked(home.data_dir):
        root = _directory(home)
        manifest_path = root / MANIFEST_FILE
        key_path = root / KEY_FILE
        if (
            manifest_path.exists()
            or key_path.exists()
            or manifest_path.is_symlink()
            or key_path.is_symlink()
            or (root / PENDING_FILE).exists()
            or (root / PENDING_FILE).is_symlink()
        ):
            if not manifest_path.exists():
                raise IdentityUnavailable("incomplete", "Community identity needs backup recovery")
            return _load(root)
        if holds_identity(Path(home.data_dir) / DIRECTORY):
            raise IdentityUnavailable("pending", "Move the incoming community identity first")
        private_key = Ed25519PrivateKey.generate()
        private_raw = private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        public = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        identity = InstallationIdentity(
            str(uuid.uuid4()),
            "sha256:" + hashlib.sha256(public).hexdigest(),
            public,
            None,
            None,
            "unenrolled",
            private_key,
        )
        manifest = _manifest_for(identity)
        _atomic_write(manifest_path, json.dumps(manifest, separators=(",", ":")).encode("ascii"))
        _atomic_write(key_path, base64.b64encode(private_raw) + b"\n")
        return _load(root)
