"""Offline schema inspection, upgrade, and recovery export commands."""

from pathlib import Path

from scanner.persistence.migrations.adoption import export_upgrade_recovery
from scanner.persistence.migrations.preflight import (
    database_location,
    inspect_schema,
    prepare_database,
)
from scanner.runtime.maintenance import DatabaseMaintenance


def run_migration_command(action: str, destination: str | None = None) -> str:
    """Return an operator-facing result without opening application sessions."""
    database_path, data_dir = database_location()
    if action == "inspect":
        with DatabaseMaintenance(data_dir).worker_lease():
            revision = inspect_schema(database_path, data_dir)
        return f"Database: {database_path}\nSchema: {revision}"
    if action == "upgrade":
        revision = prepare_database()
        return f"Database: {database_path}\nSchema: {revision}"
    if action == "recover-export" and destination is not None:
        target = Path(destination).resolve()
        if target.is_relative_to(data_dir):
            raise ValueError("Recovery destination must be outside the active data directory")
        exported = export_upgrade_recovery(database_path, data_dir, target)
        return (
            f"Recovery copy: {exported}\nInspect and upgrade this copy in its own "
            "data directory before switching configuration."
        )
    raise ValueError("Unknown migration command")
