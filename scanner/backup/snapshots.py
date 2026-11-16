"""Private, consistent SQLite safety snapshots for local schema upgrades."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from contextlib import closing, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from scanner.persistence.migrations.foreign_keys import foreign_key_failures
from scanner.runtime.durability import sync_directory as _sync_directory
from scanner.runtime.maintenance import DatabaseMaintenance
from scanner.runtime.runtime_paths import ensure_user_private_directory

FORMAT_VERSION = 1
DATABASE_NAME = "snapshot.sqlite3"
MANIFEST_NAME = "manifest.json"
SNAPSHOT_DIRECTORY = "migration-snapshots"


class SnapshotError(RuntimeError):
    """A recovery snapshot cannot be created or safely used."""


def _validate(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise SnapshotError("SQLite integrity validation failed")
    summary = foreign_key_failures(connection)
    if summary:
        raise SnapshotError(f"SQLite foreign key validation failed: {summary}")


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_root(data_dir: Path, *, create: bool) -> Path:
    root = Path(data_dir) / SNAPSHOT_DIRECTORY
    if root.is_symlink():
        raise SnapshotError("Snapshot directory must not be a symbolic link")
    if create:
        ensure_user_private_directory(root)
    if root.exists() and (
        not root.is_dir() or (os.name == "posix" and stat.S_IMODE(root.stat().st_mode) & 0o077)
    ):
        raise SnapshotError("Snapshot directory must be owner-only")
    return root


def create_safety_snapshot(database_path: Path, data_dir: Path, *, timeout: float = 10.0) -> Path:
    """Publish one verified copy without creating or replacing the active database."""
    database_path = Path(database_path)
    if database_path.is_symlink() or not database_path.is_file():
        raise SnapshotError("A regular existing SQLite database is required")
    maintenance = DatabaseMaintenance(data_dir)
    lease = (
        nullcontext() if maintenance.owns_exclusive() else maintenance.exclusive(timeout=timeout)
    )
    with lease:
        if database_path.is_symlink() or not database_path.is_file():
            raise SnapshotError("A regular existing SQLite database is required")
        root = _snapshot_root(data_dir, create=True)
        stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=root))
        generation = uuid4().hex
        target = root / f"snapshot-{generation}"
        published = False
        try:
            if target.exists():
                raise SnapshotError("Recovery generation already exists")
            database_copy = stage / DATABASE_NAME
            descriptor = os.open(database_copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            source_uri = f"{database_path.resolve().as_uri()}?mode=ro"
            with closing(sqlite3.connect(source_uri, uri=True)) as source:
                with closing(sqlite3.connect(database_copy)) as destination:
                    source.backup(destination)
                    destination.execute("PRAGMA journal_mode=DELETE")
                    _validate(destination)
            with database_copy.open("rb") as database_file:
                os.fsync(database_file.fileno())
            manifest = {
                "format_version": FORMAT_VERSION,
                "generation": generation,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "database": DATABASE_NAME,
                "size": database_copy.stat().st_size,
                "sha256": _checksum(database_copy),
            }
            manifest_path = stage / MANIFEST_NAME
            descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as manifest_file:
                json.dump(manifest, manifest_file, sort_keys=True)
                manifest_file.write("\n")
                manifest_file.flush()
                os.fsync(manifest_file.fileno())
            _sync_directory(stage)
            stage.rename(target)
            published = True
            _sync_directory(root)
            validate_safety_snapshot(target)
            return target
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            if published:
                shutil.rmtree(target)
            raise


def validate_safety_snapshot(generation_path: Path) -> dict:
    """Return a manifest only after verifying a published generation end-to-end."""
    generation_path = Path(generation_path)
    try:
        _snapshot_root(generation_path.parent.parent, create=False)
        if generation_path.is_symlink() or not generation_path.is_dir():
            raise SnapshotError("Recovery generation is not a private directory")
        if {item.name for item in generation_path.iterdir()} != {DATABASE_NAME, MANIFEST_NAME}:
            raise SnapshotError("Recovery generation has unexpected or missing files")
        manifest_path = generation_path / MANIFEST_NAME
        database_copy = generation_path / DATABASE_NAME
        if any(path.is_symlink() or not path.is_file() for path in (manifest_path, database_copy)):
            raise SnapshotError("Recovery generation contains an invalid file")
        if os.name == "posix" and any(
            stat.S_IMODE(path.stat().st_mode) & 0o077
            for path in (generation_path, manifest_path, database_copy)
        ):
            raise SnapshotError("Recovery generation is not owner-only")
        if manifest_path.stat().st_size > 4096:
            raise SnapshotError("Recovery manifest is too large")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or set(manifest) != {
            "format_version",
            "generation",
            "created_at",
            "database",
            "size",
            "sha256",
        }:
            raise SnapshotError("Recovery manifest is invalid")
        if (
            type(manifest["format_version"]) is not int
            or manifest["format_version"] != FORMAT_VERSION
        ):
            raise SnapshotError("Unsupported recovery manifest format")
        if (
            not isinstance(manifest["generation"], str)
            or generation_path.name != f"snapshot-{manifest['generation']}"
            or not isinstance(manifest["created_at"], str)
            or manifest["database"] != DATABASE_NAME
            or type(manifest["size"]) is not int
            or manifest["size"] <= 0
            or not isinstance(manifest["sha256"], str)
            or len(manifest["sha256"]) != 64
        ):
            raise SnapshotError("Recovery manifest is invalid")
        created_at = datetime.fromisoformat(manifest["created_at"])
        if created_at.tzinfo is None:
            raise SnapshotError("Recovery manifest timestamp is missing its timezone")
        if manifest["size"] != database_copy.stat().st_size or manifest["sha256"] != _checksum(
            database_copy
        ):
            raise SnapshotError("Recovery database does not match its manifest")
        with closing(sqlite3.connect(f"{database_copy.as_uri()}?mode=ro", uri=True)) as connection:
            _validate(connection)
        return manifest
    except (OSError, ValueError, UnicodeError, sqlite3.DatabaseError) as error:
        raise SnapshotError("Recovery snapshot validation failed") from error


def select_recovery_snapshot(data_dir: Path) -> Path | None:
    """Select the newest verified generation; never silently skip a damaged one."""
    root = _snapshot_root(data_dir, create=False)
    if not root.exists():
        return None
    generations = []
    for path in root.iterdir():
        if path.name.startswith("snapshot-"):
            manifest = validate_safety_snapshot(path)
            generations.append((datetime.fromisoformat(manifest["created_at"]), path.name, path))
    return max(generations)[2] if generations else None


def cleanup_incomplete_snapshots(data_dir: Path, *, timeout: float = 10.0) -> int:
    """Remove only unpublished staging directories while holding exclusive maintenance."""
    with DatabaseMaintenance(data_dir).exclusive(timeout=timeout):
        root = _snapshot_root(data_dir, create=False)
        if not root.exists():
            return 0
        removed = 0
        for path in root.iterdir():
            if path.name.startswith(".pending-"):
                if path.is_symlink() or not path.is_dir():
                    raise SnapshotError("Unpublished recovery generation is not a directory")
                shutil.rmtree(path)
                removed += 1
        if removed:
            _sync_directory(root)
        return removed
