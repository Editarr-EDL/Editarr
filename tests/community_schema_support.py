"""SQLite schema inspection for the supported unversioned database fixture."""

import hashlib
import json

from sqlalchemy import inspect, text


def schema_specification(engine):
    """Return stable, structured SQLite schema metadata for baseline comparison."""
    inspector = inspect(engine)
    tables = {}
    for table_name in sorted(inspector.get_table_names()):
        tables[table_name] = {
            "columns": [
                {
                    "name": column["name"],
                    "type": str(column["type"]),
                    "nullable": column["nullable"],
                    "default": column["default"],
                    "primary_key": column["primary_key"],
                }
                for column in inspector.get_columns(table_name)
            ],
            "checks": sorted(
                (
                    {"name": constraint["name"], "sqltext": " ".join(constraint["sqltext"].split())}
                    for constraint in inspector.get_check_constraints(table_name)
                ),
                key=lambda constraint: (constraint["name"] or "", constraint["sqltext"]),
            ),
            "foreign_keys": sorted(
                inspector.get_foreign_keys(table_name),
                key=lambda foreign_key: foreign_key["constrained_columns"],
            ),
            "indexes": sorted(inspector.get_indexes(table_name), key=lambda index: index["name"]),
            "primary_key": inspector.get_pk_constraint(table_name),
            "unique_constraints": sorted(
                inspector.get_unique_constraints(table_name),
                key=lambda constraint: (constraint["name"] or "", constraint["column_names"]),
            ),
        }
    return json.loads(
        json.dumps({"version": "unversioned-v1", "tables": tables}, sort_keys=True, default=str)
    )


def required_seed_inventory(connection):
    """Capture the singleton and taxonomy defaults needed for schema adoption."""
    return {
        "filter_settings": [
            list(row)
            for row in connection.execute(
                text("SELECT id, revision FROM filter_settings ORDER BY id")
            )
        ],
        "mcf_severity_defaults": [
            list(row)
            for row in connection.execute(
                text(
                    "SELECT settings_id, subcategory, position, severity "
                    "FROM mcf_severity_defaults ORDER BY position"
                )
            )
        ],
    }


def baseline_specification(engine):
    """Return the versioned schema and required seed inventory, not user rows."""
    specification = schema_specification(engine)
    with engine.connect() as connection:
        specification["required_seeds"] = required_seed_inventory(connection)
    return specification


def schema_fingerprint(specification):
    """Hash canonical structured metadata without depending on SQLite file bytes."""
    encoded = json.dumps(specification, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
