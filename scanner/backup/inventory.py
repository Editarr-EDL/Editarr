"""Allowlisted external files for portable local backups."""

from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

from scanner.security.community_identity import holds_identity, read_identity_backup_snapshot
from scanner.security.community_identity_homes import owner_home, owner_identity_root
from scanner.security.private_identity_files import HOMES, IdentityHome
from scanner.settings.model_scan_settings import MODEL_SCAN_SETTINGS_FILENAME

EXTERNAL_FILES = (
    "deployment.json",
    "diagnostics.json",
    MODEL_SCAN_SETTINGS_FILENAME,
    "secrets/auth-csrf.key",
    "secrets/api-key-wrap.key",
)
EXTERNAL_SECRETS = ("EDITARR_AUTH_CSRF_ENCRYPTION_KEY",)


def capture_external_files(
    data_dir: Path, database_path: Path
) -> tuple[dict[str, bytes], tuple[str, ...], str | None]:
    """Copy known private files and identify credentials supplied outside the archive.

    `database_path` names every identity home. The owner's identity is archived under
    the incoming directory's names and every other account's home under its reference.
    """
    identity_root = owner_identity_root(data_dir, database_path)
    if identity_root is not None:
        identity = read_identity_backup_snapshot(data_dir, identity_root)
        files = dict(identity.files)
        key_id = identity.key_id
    else:
        files = {}
        key_id = None
    files.update(_account_homes(data_dir, database_path))
    omitted = tuple(name for name in EXTERNAL_SECRETS if name in os.environ)
    for name in EXTERNAL_FILES:
        if name == "secrets/auth-csrf.key" and omitted:
            continue
        path = data_dir / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024 * 1024:
            raise ValueError("Backup external file is not a bounded regular file")
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Backup external file is not owner-only")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("Backup external file changed during capture")
            content = source.read(64 * 1024 * 1024 + 1)
        current = path.lstat()
        if len(content) > 64 * 1024 * 1024 or (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        ) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
            raise ValueError("Backup external file changed during capture")
        files[name] = content
    return files, omitted, key_id


def _account_homes(data_dir: Path, database_path: Path) -> dict[str, bytes]:
    owner = owner_home(data_dir, database_path)
    uri = f"{Path(database_path).resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as database:
        if not database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'community_identities'"
        ).fetchone():
            return {}
        references = [
            row[0]
            for row in database.execute(
                "SELECT credential_ref FROM community_identities ORDER BY credential_ref"
            )
        ]
    files = {}
    for reference in references:
        home = IdentityHome(data_dir, reference)
        if home == owner or not holds_identity(home.root):
            continue
        for member, content in read_identity_backup_snapshot(data_dir, home.root).files:
            files[f"{HOMES.as_posix()}/{reference}/{Path(member).name}"] = content
    return files
