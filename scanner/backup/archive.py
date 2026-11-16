"""Bounded portable archive for full local backups."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from scanner import __version__
from scanner.runtime.maintenance import DatabaseMaintenance, _lock, _open_lock, _unlock
from scanner.runtime.runtime_paths import ensure_user_private_directory

from .age_transport import run_age, validate_age_header
from .inventory import EXTERNAL_FILES, capture_external_files
from .snapshots import _sync_directory, _validate

MANIFEST = "manifest.json"
DATABASE = "database.sqlite3"
MAX_MEMBERS = 1024
MAX_MANIFEST = 1024 * 1024
MAX_PATH = 256
MAX_DATABASE = 64 * 1024**3
MAX_OTHER = 64 * 1024**2
MAX_TOTAL = 80 * 1024**3
IDENTITY_FILES = ("identity.json", "current.key", "pending.key")
# The owner's identity keeps the incoming directory's names; every other account's
# home is archived under its own reference.
INCOMING_MEMBERS = tuple(f"secrets/community/{name}" for name in IDENTITY_FILES)
HOME_MEMBER = re.compile(
    r"secrets/community-identities/([0-9a-f]{32})/(identity\.json|current\.key|pending\.key)"
)
ALLOWED_MEMBERS = frozenset((DATABASE, *EXTERNAL_FILES, *INCOMING_MEMBERS))


def allowed_member(name: str) -> bool:
    """Whether `name` may appear in an archive."""
    return name in ALLOWED_MEMBERS or HOME_MEMBER.fullmatch(name) is not None


STAGE_MARKER = b"editarr-backup-stage-v1\n"
SECRET_TYPES = frozenset(
    {
        "database-integration-credentials",
        "database-local-auth",
        "community-signing-key",
        "community-enrollment-confirmation",
        "community-recovery-proof",
        "local-csrf-key",
        "local-api-key-wrap-key",
    }
)


class BackupError(RuntimeError):
    """A portable archive could not be created or validated."""


class BackupPublicationUncertain(BackupError):
    """Output was replaced, but its directory sync did not confirm durability."""


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise BackupError("Duplicate backup manifest field")
        fields[key] = value
    return fields


@contextmanager
def _staging_area(parent: Path):
    """Serialize publication and recover private plaintext from interrupted exports."""
    owner = _open_lock(parent / ".editarr-backup.lock")
    acquired = False
    try:
        acquired = _lock(owner, exclusive=True, blocking=True)
        if not acquired:
            raise BackupError("Could not acquire backup publication lock")
        for candidate in parent.glob(".editarr-backup-*"):
            if (
                candidate.is_dir()
                and not candidate.is_symlink()
                and (candidate / ".stage-marker").is_file()
                and (candidate / ".stage-marker").read_bytes() == STAGE_MARKER
            ):
                shutil.rmtree(candidate)
        with tempfile.TemporaryDirectory(prefix=".editarr-backup-", dir=parent) as directory:
            stage = Path(directory)
            stage.chmod(0o700)
            (stage / ".stage-marker").write_bytes(STAGE_MARKER)
            yield stage
    finally:
        if acquired:
            _unlock(owner)
        os.close(owner)


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _sqlite_snapshot(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise BackupError("A regular SQLite database is required")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as active:
        with closing(sqlite3.connect(target)) as copy:
            active.backup(copy)
            copy.execute("PRAGMA journal_mode=DELETE")
            _validate(copy)
    with target.open("rb") as handle:
        os.fsync(handle.fileno())


def _members(database: Path, external: dict[str, bytes]) -> dict[str, Path | bytes]:
    members: dict[str, Path | bytes] = {DATABASE: database}
    members.update(external)
    if len(members) + 1 > MAX_MEMBERS or not all(map(allowed_member, members)):
        raise BackupError("Too many backup members")
    total = 0
    for name, content in members.items():
        size = content.stat().st_size if isinstance(content, Path) else len(content)
        if len(name.encode("utf-8")) > MAX_PATH or name.startswith("/") or ".." in name.split("/"):
            raise BackupError("Invalid backup member path")
        if size > (MAX_DATABASE if name == DATABASE else MAX_OTHER):
            raise BackupError("Backup member exceeds its size limit")
        total += size
    if total + MAX_MANIFEST > MAX_TOTAL:
        raise BackupError("Backup exceeds the total size limit")
    return members


def _write_archive(path: Path, manifest: dict, members: dict[str, Path | bytes]) -> None:
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_MANIFEST:
        raise BackupError("Backup manifest exceeds its size limit")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        with tarfile.open(fileobj=stream, mode="w|") as archive:
            for name, content in ((MANIFEST, encoded), *members.items()):
                info = tarfile.TarInfo(name)
                info.size = content.stat().st_size if isinstance(content, Path) else len(content)
                info.mode = 0o600
                if isinstance(content, Path):
                    with content.open("rb") as reader:
                        archive.addfile(info, reader)
                else:
                    archive.addfile(info, io.BytesIO(content))
        stream.flush()
        os.fsync(stream.fileno())


def validate_plain_archive(
    path: Path, *, protection: str = "plain", stage: Path | None = None
) -> dict:
    """Stream-check the allowlisted archive, optionally staging the checked bytes."""
    with path.open("rb") as source:
        with tarfile.open(fileobj=source, mode="r|") as archive:
            header = archive.next()
            if (
                header is None
                or header.name != MANIFEST
                or not header.isfile()
                or header.size > MAX_MANIFEST
            ):
                raise BackupError("Invalid backup manifest member")
            handle = archive.extractfile(header)
            if handle is None:
                raise BackupError("Missing backup manifest")
            manifest = json.loads(handle.read(MAX_MANIFEST + 1), object_pairs_hook=_unique_fields)
            if not isinstance(manifest, dict) or manifest.get("format") != "editarr-backup-1":
                raise BackupError("Unsupported backup format")
            if (
                manifest.get("kind") not in ("full", "settings")
                or manifest.get("protection") != protection
            ):
                raise BackupError("Invalid backup kind or protection")
            if (
                not isinstance(manifest.get("schema_revision"), str)
                or len(manifest["schema_revision"]) > 64
                or type(manifest.get("identity_included")) is not bool
                or (
                    manifest.get("installation_id") is not None
                    and (
                        not isinstance(manifest["installation_id"], str)
                        or len(manifest["installation_id"]) > 36
                    )
                )
                or (
                    manifest.get("identity_key_id") is not None
                    and (
                        not isinstance(manifest["identity_key_id"], str)
                        or len(manifest["identity_key_id"]) > 128
                    )
                )
                or not isinstance(manifest.get("omitted_environment"), list)
                or any(
                    value != "EDITARR_AUTH_CSRF_ENCRYPTION_KEY"
                    for value in manifest["omitted_environment"]
                )
                or len(manifest["omitted_environment"]) > 1
                or not isinstance(manifest.get("included_secret_types"), list)
                or any(
                    not isinstance(value, str) or value not in SECRET_TYPES
                    for value in manifest["included_secret_types"]
                )
                or len(manifest["included_secret_types"]) > len(SECRET_TYPES)
            ):
                raise BackupError("Invalid backup manifest metadata")
            expected = manifest.get("files")
            if (
                not isinstance(expected, dict)
                or DATABASE not in expected
                or len(expected) + 1 > MAX_MEMBERS
                or not all(map(allowed_member, expected))
            ):
                raise BackupError("Invalid backup inventory")
            seen = set()
            total = header.size
            while (member := archive.next()) is not None:
                name = member.name
                if (
                    name in seen
                    or name not in expected
                    or not member.isfile()
                    or len(name.encode("utf-8")) > MAX_PATH
                    or name.startswith("/")
                    or ".." in name.split("/")
                    or member.size > (MAX_DATABASE if name == DATABASE else MAX_OTHER)
                ):
                    raise BackupError("Unexpected backup member")
                seen.add(name)
                total += member.size
                if total > MAX_TOTAL:
                    raise BackupError("Backup exceeds the total size limit")
                entry = expected[name]
                if (
                    not isinstance(entry, dict)
                    or type(entry.get("size")) is not int
                    or entry["size"] != member.size
                ):
                    raise BackupError("Backup member size mismatch")
                digest = hashlib.sha256()
                content = archive.extractfile(member)
                if content is None:
                    raise BackupError("Missing backup member")
                if stage is not None:
                    output = stage / name
                    parent = stage
                    for segment in Path(name).parts[:-1]:
                        parent = parent / segment
                        parent.mkdir(mode=0o700, exist_ok=True)
                    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "wb") as writer:
                        for chunk in iter(lambda: content.read(1024 * 1024), b""):
                            digest.update(chunk)
                            writer.write(chunk)
                else:
                    for chunk in iter(lambda: content.read(1024 * 1024), b""):
                        digest.update(chunk)
                if entry.get("sha256") != digest.hexdigest():
                    raise BackupError("Backup member checksum mismatch")
            if seen != set(expected):
                raise BackupError("Backup is missing members")
            end_of_members = archive.offset
        # Require exactly the standard two zero end blocks and record padding.
        expected_size = ((end_of_members + 1024 + 10239) // 10240) * 10240
        if path.stat().st_size != expected_size:
            raise BackupError("Backup has trailing data")
        source.seek(end_of_members)
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            if any(chunk):
                raise BackupError("Backup has nonzero trailing data")
    return manifest


def export_backup(
    database_path: Path,
    data_dir: Path,
    destination: Path,
    *,
    passphrase: str | None = None,
    replace_existing: bool = True,
    require_reconciled: bool = False,
) -> dict:
    """Publish a validated private backup, leaving existing output intact on failure."""
    if passphrase is not None and not passphrase:
        raise BackupError("Encrypted export requires a nonempty passphrase")
    destination = Path(destination)
    if destination.is_symlink():
        raise BackupError("Backup destination must not be a symbolic link")
    ensure_user_private_directory(destination.parent)
    with _staging_area(destination.parent) as stage:
        with DatabaseMaintenance(data_dir).exclusive():
            if require_reconciled and any(
                (data_dir / name).exists()
                for name in (
                    "restore-activation.json",
                    "restore-reconciliation.json",
                )
            ):
                raise BackupError("Restore reconciliation is required before scheduled backups")
            database = stage / DATABASE
            _sqlite_snapshot(database_path, database)
            from scanner.persistence.migrations.adoption import _inspect_database

            source_revision = _inspect_database(database) or "unversioned-v1"
            external, omitted, key_id = capture_external_files(data_dir, database)
            identity_manifest = (
                json.loads(external["secrets/community/identity.json"])
                if key_id is not None
                else {}
            )
            installation_id = identity_manifest["installation_id"] if key_id is not None else None
            members = _members(database, external)
            manifest = {
                "format": "editarr-backup-1",
                "app_version": __version__,
                "schema_revision": source_revision,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "kind": "full",
                "protection": "age-scrypt" if passphrase is not None else "plain",
                "identity_included": key_id is not None,
                "identity_key_id": key_id,
                "installation_id": installation_id,
                "portability": {
                    "deployment_and_media_paths": "review_on_restore",
                    "media_sidecars": "excluded",
                    "environment_credentials": "reconfigure",
                },
                "included_secret_types": [
                    "database-integration-credentials",
                    "database-local-auth",
                    *(["community-signing-key"] if key_id is not None else []),
                    *(
                        ["community-enrollment-confirmation"]
                        if (identity_manifest.get("client_state") or {}).get("pending")
                        else []
                    ),
                    *(["local-csrf-key"] if "secrets/auth-csrf.key" in external else []),
                    *(["local-api-key-wrap-key"] if "secrets/api-key-wrap.key" in external else []),
                    *(["community-recovery-proof"] if identity_manifest.get("recovery") else []),
                ],
                "omitted_environment": omitted,
                "files": {
                    name: {
                        "size": content.stat().st_size
                        if isinstance(content, Path)
                        else len(content),
                        "sha256": _digest(content)
                        if isinstance(content, Path)
                        else hashlib.sha256(content).hexdigest(),
                    }
                    for name, content in members.items()
                },
            }
            archive = stage / "archive.tar"
            _write_archive(archive, manifest, members)
            if capture_external_files(data_dir, database) != (external, omitted, key_id):
                raise BackupError("External settings changed during backup capture")
        validated = validate_plain_archive(
            archive, protection="age-scrypt" if passphrase is not None else "plain"
        )
        if passphrase is not None:
            ciphertext = stage / "archive.age"
            run_age(archive, ciphertext, passphrase)
            validate_age_header(ciphertext)
            checked = stage / "checked.tar"
            run_age(ciphertext, checked, passphrase, decrypt=True)
            if (
                _digest(checked) != _digest(archive)
                or validate_plain_archive(checked, protection="age-scrypt") != validated
            ):
                raise BackupError("Encrypted backup verification failed")
            ciphertext.chmod(0o600)
            archive = ciphertext
        if replace_existing:
            os.replace(archive, destination)
        else:
            os.link(archive, destination)
            archive.unlink()
        try:
            _sync_directory(destination.parent)
        except OSError as error:
            raise BackupPublicationUncertain(
                "Backup output was replaced but durable publication could not be confirmed"
            ) from error
        return validated
