"""Whether an installation is empty enough to receive a restore.

Restore only fills an empty installation (ruling wave14-fresh-install-restore):
no accounts, no work, and no community identity. Onboarding, the HTTP setup
flow, and the CLI all ask `require_empty_installation` before any change, and
activation asks again under the maintenance lock.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from scanner.persistence.migrations.adoption import BASELINE_REVISION, _inspect_database
from scanner.persistence.migrations.schema import migrated_in_memory
from scanner.security.community_identity import holds_identity
from scanner.security.private_identity_files import DIRECTORY, HOMES

from .archive import BackupError

# Tables the migrations seed. They count as empty only while they hold exactly the seeds.
SEEDED_TABLES = (
    "community_identities",
    "filter_settings",
    "mcf_severity_defaults",
    "media_access_scopes",
)
# Seed columns that differ between two fresh installations.
_UNSEEDED_COLUMNS = frozenset({"created_at", "updated_at", "credential_ref"})
HAS_ACCOUNTS = "restore_target_has_accounts"
HAS_WORK = "restore_target_has_work"
HAS_IDENTITY = "restore_target_has_identity"
UNSUPPORTED_SCHEMA = "restore_target_unsupported_schema"


class RestoreRefused(BackupError):
    """A restore was refused before any change; `reason` is a closed code."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def require_empty_installation(data_dir: Path, database_path: Path) -> None:
    """Refuse a target that holds accounts, work, a community identity, or an unknown schema.

    A missing database is empty. An existing one must be a supported schema, so a
    rollback can always put it back, and its seeded tables must hold only the
    rows the migrations wrote.
    """
    data_dir, database_path = Path(data_dir), Path(database_path)
    if database_path.exists() or database_path.is_symlink():
        _require_empty_database(database_path)
    homes = data_dir / HOMES
    roots = [data_dir / DIRECTORY, *(homes.iterdir() if homes.is_dir() else ())]
    if any(root.is_symlink() or (root.is_dir() and holds_identity(root)) for root in roots):
        raise RestoreRefused(HAS_IDENTITY, "This installation already has a community identity")


def _require_empty_database(database_path: Path) -> None:
    try:
        revision = _inspect_database(database_path)
    except (ValueError, sqlite3.DatabaseError) as error:
        raise RestoreRefused(
            UNSUPPORTED_SCHEMA, f"This installation's database is not a supported schema: {error}"
        ) from None
    seeds = _fresh_seeds(revision or BASELINE_REVISION)
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as database:
        tables = [
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version' ORDER BY name"
            )
        ]
        if "users" in tables and database.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise RestoreRefused(HAS_ACCOUNTS, "This installation already has accounts")
        for table in tables:
            if table in SEEDED_TABLES:
                holds_work = _seed_rows(database, table) != seeds[table]
            else:
                holds_work = database.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
            if holds_work:
                raise RestoreRefused(HAS_WORK, f"This installation already has data in {table}")


def _seed_rows(database: sqlite3.Connection, table: str) -> list[tuple]:
    """A seeded table's rows without the columns that differ between fresh installations."""
    columns = ", ".join(
        f'"{row[1]}"'
        for row in database.execute(f'PRAGMA table_info("{table}")')
        if row[1] not in _UNSEEDED_COLUMNS
    )
    return database.execute(f'SELECT {columns} FROM "{table}" ORDER BY {columns}').fetchall()


@lru_cache(maxsize=4)
def _fresh_seeds(revision: str) -> dict[str, list[tuple]]:
    """Run the packaged migrations to `revision` in memory and read the rows they seed."""
    with migrated_in_memory(revision) as connection:
        database = connection.connection.driver_connection
        present = {row[0] for row in database.execute("SELECT name FROM sqlite_master")}
        return {table: _seed_rows(database, table) for table in SEEDED_TABLES if table in present}
