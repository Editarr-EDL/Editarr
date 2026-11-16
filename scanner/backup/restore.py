"""Isolated portable-backup preflight and redacted restore preview."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import tempfile
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from scanner.persistence.migrations.adoption import HEAD_REVISION, _inspect_database
from scanner.runtime.maintenance import _lock, _unlock
from scanner.runtime.runtime_paths import ensure_user_private_directory
from scanner.security.community_identity import _load
from scanner.security.community_identity_homes import _OWNER_REFERENCE
from scanner.security.private_identity_files import DIRECTORY, HOMES
from scanner.settings.deployment_config import _decode_overrides

from .account_family import check_accounts
from .age_transport import run_age, validate_age_header
from .archive import (
    DATABASE,
    HOME_MEMBER,
    MAX_TOTAL,
    BackupError,
    allowed_member,
    validate_plain_archive,
)
from .restore_target import RestoreRefused, require_empty_installation

_AGE_MAGIC = b"age-encryption.org/v1\n"
_TAR_MAGIC = b"manifest.json"
_STAGE_MARKER = b"editarr-restore-stage-v1\n"
SETTINGS_BACKUP = "restore_settings_backup"
OLD_SCHEMA = "restore_backup_schema_unsupported"
UNKNOWN_HOME = "restore_unknown_identity_home"


def backup_deployment(stage: Path) -> dict:
    """The backup's saved base URL and port, which a restore reports but does not apply.

    The empty installation keeps its own binding (ruling wave14-mu40-deployment-binding).
    `None` means the backup used the default.
    """
    path = stage / "deployment.json"
    if not path.is_file():
        return {"base_url": None, "port": None}
    try:
        overrides = _decode_overrides(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as error:
        raise BackupError("Backup deployment configuration is invalid") from error
    return {"base_url": overrides.base_url, "port": overrides.port}


@dataclass(frozen=True)
class RestorePreview:
    """A private staged generation, valid only while the context manager is open."""

    stage: Path
    plan: dict


@contextmanager
def _private_stage(parent: Path) -> Iterator[Path]:
    ensure_user_private_directory(parent)
    descriptor = os.open(
        parent / ".editarr-restore.lock",
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BackupError("Invalid restore staging lock")
        acquired = _lock(descriptor, exclusive=True, blocking=True)
        if not acquired:
            raise BackupError("Could not acquire restore staging lock")
        for candidate in parent.glob(".editarr-restore-*"):
            if (
                candidate.is_dir()
                and not candidate.is_symlink()
                and (os.name != "posix" or stat.S_IMODE(candidate.stat().st_mode) == 0o700)
                and (candidate / ".stage-marker").is_file()
                and not (candidate / ".stage-marker").is_symlink()
                and (candidate / ".stage-marker").read_bytes() == _STAGE_MARKER
            ):
                shutil.rmtree(candidate)
        with tempfile.TemporaryDirectory(prefix=".editarr-restore-", dir=parent) as directory:
            stage = Path(directory)
            stage.chmod(0o700)
            (stage / ".stage-marker").write_bytes(_STAGE_MARKER)
            (stage / ".stage-marker").chmod(0o600)
            yield stage
    finally:
        if acquired:
            _unlock(descriptor)
        os.close(descriptor)


def identity_files(root: Path, names: set[str]) -> dict | None:
    """Validate one staged identity directory holding exactly `names`, or nothing."""
    if not names:
        return None
    if "identity.json" not in names:
        raise BackupError("Incomplete community identity inventory")
    try:
        identity = _load(root, allow_missing_private=True)
        if identity.private_key is None and not (
            identity.recovery and identity.recovery["state"] == "pending"
        ):
            raise BackupError("Missing community signing key without recovery intent")
    except (OSError, ValueError, RuntimeError) as error:
        raise BackupError("Invalid staged community identity") from error
    expected = {"identity.json"}
    if identity.private_key is not None:
        expected.add(identity.key_file)
    if identity.pending_replacement is not None:
        expected.add("pending.key" if identity.key_file == "current.key" else "current.key")
    if names != expected:
        raise BackupError("Inconsistent community key inventory")
    return {"installation_id": identity.installation_id, "key_id": identity.key_id}


def staged_identities(stage: Path, files) -> tuple[dict | None, dict[str, dict]]:
    """Validate the owner's identity and every other account's home in a stage."""
    owner_names = {Path(name).name for name in files if name.startswith(f"{DIRECTORY.as_posix()}/")}
    homes: dict[str, set[str]] = {}
    for name in files:
        if match := HOME_MEMBER.fullmatch(name):
            homes.setdefault(match[1], set()).add(match[2])
    owner = identity_files(stage / DIRECTORY, owner_names)
    return owner, {
        reference: identity_files(stage / HOMES / reference, names)
        for reference, names in sorted(homes.items())
    }


def _check_homes(database: sqlite3.Connection, references) -> None:
    """Every archived home must belong to an account other than the owner."""
    for reference in references:
        row = database.execute(
            "SELECT 1 FROM community_identities JOIN users ON users.id = user_id "
            "WHERE credential_ref = ? AND users.role != 'owner'",
            (reference,),
        ).fetchone()
        if row is None:
            raise RestoreRefused(UNKNOWN_HOME, "A backup identity home names no account")


def check_registrations(database: sqlite3.Connection, stage: Path) -> None:
    """Every enrolled connection must match the identity its reference names."""
    owner = database.execute(_OWNER_REFERENCE).fetchone()
    registrations = database.execute(
        "SELECT credential_ref, service_url, contributor_id, installation_id, key_id "
        "FROM community_connections WHERE contributor_id IS NOT NULL"
    ).fetchall()
    for reference, service_url, contributor_id, installation_id, key_id in registrations:
        root = stage / DIRECTORY if owner and reference == owner[0] else stage / HOMES / reference
        try:
            identity = json.loads((root / "identity.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as error:
            raise BackupError("Restored community registration has no matching identity") from error
        if not isinstance(identity, dict):
            raise BackupError("Restored community registration has no matching identity")
        rotation = identity.get("rotation") or identity.get("recovery")
        projected_key_id = (
            rotation.get("localOldKeyId", rotation.get("oldKeyId"))
            if isinstance(rotation, dict) and rotation.get("state") == "completed"
            else None
        )
        if (service_url, contributor_id, installation_id) != (
            identity.get("service_url"),
            identity.get("contributor_id"),
            identity.get("installation_id"),
        ) or key_id not in {identity.get("key_id"), projected_key_id} - {None}:
            raise BackupError("Restored community registration does not match its identity")


def _require_current_schema(path: Path) -> str:
    """Refuse a backup from any schema but head; restore never migrates an old backup."""
    revision = _inspect_database(path) or "unversioned-v1"
    if revision != HEAD_REVISION:
        raise RestoreRefused(
            OLD_SCHEMA,
            f"This backup has database schema {revision}. This Editarr restores only "
            f"backups made at schema {HEAD_REVISION}.",
        )
    return revision


@contextmanager
def preview_restore(
    archive_path: Path,
    target_data_dir: Path,
    *,
    target_database: Path | None = None,
    passphrase: str | None = None,
    staging_parent: Path | None = None,
) -> Iterator[RestorePreview]:
    """Validate and stage a backup for an empty installation without modifying it.

    The caller must retain the context through activation. Exiting it removes all
    decrypted and migrated content, including on validation failure.
    """
    archive_path = Path(archive_path)
    target_data_dir = Path(target_data_dir)
    target_database = Path(target_database) if target_database else target_data_dir / "scanner.db"
    require_empty_installation(target_data_dir, target_database)
    if archive_path.is_symlink() or not archive_path.is_file():
        raise BackupError("A regular backup file is required")
    with archive_path.open("rb") as source:
        prefix = source.read(len(_AGE_MAGIC))
    if prefix == _AGE_MAGIC:
        protection = "age-scrypt"
        if archive_path.stat().st_size > MAX_TOTAL + 2 * 1024 * 1024:
            raise BackupError("Encrypted backup exceeds the archive limit")
        validate_age_header(archive_path)
        if not passphrase:
            raise BackupError("Encrypted backup requires a passphrase")
    elif prefix.startswith(_TAR_MAGIC):
        protection = "plain"
        if passphrase is not None:
            raise BackupError("Plain backup does not accept a passphrase")
    else:
        raise BackupError("Unsupported backup container")

    staging_parent = (
        Path(staging_parent)
        if staging_parent
        else Path(tempfile.gettempdir())
        / (f"editarr-restore-{os.getuid()}" if hasattr(os, "getuid") else "editarr-restore")
    )
    ensure_user_private_directory(staging_parent)
    required_space = archive_path.stat().st_size * (3 if protection == "age-scrypt" else 2)
    if shutil.disk_usage(staging_parent).free < required_space:
        raise BackupError("Insufficient staging space for backup validation")

    with _private_stage(staging_parent) as stage:
        source_path = archive_path
        if protection == "age-scrypt":
            source_path = stage / "decrypted.tar"
            run_age(archive_path, source_path, passphrase, decrypt=True)
            if source_path.stat().st_size > MAX_TOTAL + 1024 * 1024:
                raise BackupError("Decrypted backup exceeds the archive limit")
        content = stage / "content"
        content.mkdir(mode=0o700)
        if shutil.disk_usage(stage).free < source_path.stat().st_size:
            raise BackupError("Insufficient staging space for backup validation")
        manifest = validate_plain_archive(source_path, protection=protection, stage=content)
        if manifest["kind"] != "full":
            raise RestoreRefused(SETTINGS_BACKUP, "Settings-only backups can no longer be restored")
        files = manifest["files"]
        if not all(map(allowed_member, files)):
            raise BackupError("Unexpected backup inventory")
        original_revision = _require_current_schema(content / DATABASE)
        if manifest.get("schema_revision") != original_revision:
            raise BackupError("Backup schema revision does not match its database")
        identity, homes = staged_identities(content, files)
        if manifest.get("identity_included") is not (identity is not None) or manifest.get(
            "identity_key_id"
        ) != (identity["key_id"] if identity else None):
            raise BackupError("Backup identity manifest does not match its keys")
        if "installation_id" in manifest and manifest["installation_id"] != (
            identity["installation_id"] if identity else None
        ):
            raise BackupError("Backup installation ID does not match its identity")
        uri = f"{(content / DATABASE).resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as staged:
            administrator_included = check_accounts(staged)
            _check_homes(staged, homes)
        yield RestorePreview(
            content,
            {
                "format": manifest["format"],
                "kind": "full",
                "protection": protection,
                "source_schema": original_revision,
                "staged_schema": HEAD_REVISION,
                "source_identity": identity,
                "account_identities": len(homes),
                "omitted_environment": manifest.get("omitted_environment", []),
                "included_secret_types": manifest.get("included_secret_types", []),
                "administrator_included": administrator_included,
                "backup_deployment": backup_deployment(content),
            },
        )
