"""Startup and offline admission for the supported local SQLite schema."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy.engine import make_url

from scanner.persistence.migrations.adoption import (
    HEAD_REVISION,
    JOURNAL_NAME,
    _inspect_database,
    upgrade_existing_database,
)
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.runtime.runtime_paths import ensure_user_private_directory
from scanner.settings.config import settings


def database_location() -> tuple[Path, Path]:
    """Resolve the configured on-disk SQLite file and its maintenance directory."""
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("Migrations require a file-backed SQLite database")
    return Path(os.path.abspath(url.database)), Path(settings.data_dir).resolve()


def inspect_schema(database_path: Path, data_dir: Path) -> str:
    """Read-only compatibility check; an unresolved upgrade blocks all users."""
    if (data_dir / "restore-activation.json").exists():
        raise RuntimeError("Restore activation needs offline recovery before database admission")
    journal = data_dir / JOURNAL_NAME
    if journal.exists():
        state = json.loads(journal.read_text(encoding="utf-8"))
        if (
            state.get("database") != str(database_path.resolve())
            or state.get("status") != "complete"
        ):
            raise RuntimeError(
                f"Upgrade journal unresolved at {journal}; stop the service and export recovery"
            )
    if not database_path.exists() and not database_path.is_symlink():
        return "missing"
    revision = _inspect_database(database_path)
    return revision or "unversioned-v1"


def prepare_database() -> str:
    """Coordinate fresh creation or adoption before any ordinary database access."""
    database_path, data_dir = database_location()
    ensure_user_private_directory(data_dir)
    if (data_dir / JOURNAL_NAME).exists():
        inspect_schema(database_path, data_dir)
    if not database_path.exists() and not database_path.is_symlink():
        try:
            migrate_fresh_database(database_path, data_dir)
        except FileExistsError:
            pass
    else:
        upgrade_existing_database(database_path, data_dir)
    revision = inspect_schema(database_path, data_dir)
    if revision != HEAD_REVISION:
        upgrade_existing_database(database_path, data_dir)
        revision = inspect_schema(database_path, data_dir)
    if revision != HEAD_REVISION:
        raise RuntimeError(f"Database schema is {revision}; start the service to upgrade it")
    return revision


def require_supported_schema() -> str:
    """Worker admission without migrations or database writes."""
    database_path, data_dir = database_location()
    if (data_dir / "restore-reconciliation.json").exists():
        raise RuntimeError("Restored work requires reconciliation before worker admission")
    revision = inspect_schema(database_path, data_dir)
    if revision != HEAD_REVISION:
        raise RuntimeError(f"Database schema is {revision}; start the service to upgrade it")
    return revision


def require_owner_setup() -> None:
    """Keep scan workers stopped until local owner setup; reads only."""
    database_path, _ = database_location()
    with closing(
        sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    ) as database:
        if database.execute("SELECT 1 FROM users WHERE role = 'owner'").fetchone() is None:
            raise RuntimeError("Owner setup is required before scan workers run")
