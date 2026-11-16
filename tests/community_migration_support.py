"""Regenerate frozen migration inputs only from the disposable CSS-01 fixture."""

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path

from tests.community_schema_fixture import create_unversioned_fixture
from tests.community_schema_support import baseline_specification, schema_fingerprint


def frozen_baseline():
    """Capture SQLite DDL and seed values without exposing any fixture user rows."""
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "baseline.db"
        engine = create_unversioned_fixture(database_path)
        try:
            specification = baseline_specification(engine)
        finally:
            engine.dispose()
        with sqlite3.connect(database_path) as connection:
            definitions = connection.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE type IN ('table', 'index') AND sql IS NOT NULL "
                "AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name"
            ).fetchall()
    return {
        "format_version": 1,
        "schema_fingerprint": schema_fingerprint(specification),
        "statements": [statement for _kind, _name, statement in definitions],
        "required_seeds": specification["required_seeds"],
    }


def main():
    parser = argparse.ArgumentParser(description="Generate frozen current-model baseline DDL")
    parser.add_argument("destination", type=Path, help="new baseline JSON resource path")
    args = parser.parse_args()
    if args.destination.exists():
        parser.error("Refusing to replace an existing frozen migration resource")
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(json.dumps(frozen_baseline(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
