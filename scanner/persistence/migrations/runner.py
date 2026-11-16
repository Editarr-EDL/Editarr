"""Explicit fresh-database migration entry point; adoption belongs to CSS-05."""

import threading
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, inspect

from scanner.runtime.maintenance import DatabaseMaintenance

# Alembic's `context` and `op` are process-global, so in-process commands take turns.
_ALEMBIC = threading.Lock()


def run_packaged(operation, connection: Connection, revision: str) -> None:
    """Run an Alembic command such as `command.upgrade` on `connection` with packaged revisions."""
    configuration = Config()
    configuration.set_main_option("script_location", str(Path(__file__).resolve().parent))
    configuration.attributes["connection"] = connection
    with _ALEMBIC:
        operation(configuration, revision)


def upgrade_connection(connection: Connection, revision: str = "head") -> None:
    """Upgrade an already-coordinated SQLite connection using packaged revisions."""
    existing_tables = set(inspect(connection).get_table_names())
    if existing_tables and "alembic_version" not in existing_tables:
        raise RuntimeError("Unversioned database needs verified adoption before migration")
    run_packaged(command.upgrade, connection, revision)


def migrate_fresh_database(database_path: Path, data_dir: Path, *, timeout: float = 10.0) -> None:
    """Create the baseline only at a nonexistent path under exclusive maintenance."""
    database_path = Path(database_path)
    if database_path.exists() or database_path.is_symlink():
        raise FileExistsError("Fresh migration refuses an existing database")
    with DatabaseMaintenance(data_dir).exclusive(timeout=timeout):
        if database_path.exists() or database_path.is_symlink():
            raise FileExistsError("Fresh migration refuses an existing database")
        engine = create_engine(f"sqlite:///{database_path}")
        try:
            with engine.begin() as connection:
                upgrade_connection(connection)
        finally:
            engine.dispose()
