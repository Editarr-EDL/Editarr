"""SQLite shape and seed identities, with frozen packaged baseline verification."""

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from importlib.resources import files

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

# sqlite_master keeps comments, literals, and quoted names verbatim; match them whole.
_SQL_TOKENS = re.compile(
    r"--[^\n]*|/\*.*?(?:\*/|\Z)|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`(?:[^`]|``)*`|\[[^\]]*\]|\w+",
    re.S,
)


def _autoincrement_tables(connection):
    """Name tables whose DDL uses the AUTOINCREMENT keyword, which reflection omits."""
    return {
        name
        for name, sql in connection.execute(
            text("SELECT name, sql FROM sqlite_master WHERE type = 'table'")
        )
        if any(token.upper() == "AUTOINCREMENT" for token in _SQL_TOKENS.findall(sql or ""))
    }


_TABLE_CACHE_LIMIT = 64
_table_cache: dict[tuple[str, bool], str] = {}


def _schema_digest(connection):
    """Hash the stored DDL from which SQLite derives every reflected table property."""
    rows = connection.execute(
        text("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name, tbl_name")
    )
    return hashlib.sha256(json.dumps([list(row) for row in rows]).encode()).hexdigest()


def _table_specification(connection, versioned):
    """Reflect tables once per distinct DDL; any schema change produces a new digest."""
    key = (_schema_digest(connection), versioned)
    cached = _table_cache.get(key)
    if cached is None:
        cached = json.dumps(_reflect_tables(connection, versioned), sort_keys=True, default=str)
        if len(_table_cache) >= _TABLE_CACHE_LIMIT:
            _table_cache.pop(next(iter(_table_cache)), None)
        _table_cache[key] = cached
    return json.loads(cached)


def schema_specification(connection, *, versioned=False, seed_values=True):
    """Reflect tables, constraints, indexes and seed rows without user data.

    ``autoincrement`` appears only on tables that declare it, so specifications
    and frozen fingerprints of schemas without it are unchanged.
    ``seed_values=False`` excludes editable values from database admission.
    """
    tables = _table_specification(connection, versioned)
    settings_columns = "id, revision" if seed_values else "id"
    severity_columns = "settings_id, subcategory, position"
    if seed_values:
        severity_columns += ", severity"
    seeds = {
        "filter_settings": [
            list(row)
            for row in connection.execute(
                text(f"SELECT {settings_columns} FROM filter_settings ORDER BY id")
            )
        ],
        "mcf_severity_defaults": [
            list(row)
            for row in connection.execute(
                text(f"SELECT {severity_columns} FROM mcf_severity_defaults ORDER BY position")
            )
        ],
    }
    # A partial index reflects its predicate as a SQL clause; compare its text.
    return json.loads(
        json.dumps(
            {"version": "unversioned-v1", "tables": tables, "required_seeds": seeds},
            sort_keys=True,
            default=str,
        )
    )


def _reflect_tables(connection, versioned):
    inspector = inspect(connection)
    autoincrement = _autoincrement_tables(connection)
    tables = {}
    for table_name in sorted(inspector.get_table_names()):
        if versioned and table_name == "alembic_version":
            continue
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
                    {"name": item["name"], "sqltext": " ".join(item["sqltext"].split())}
                    for item in inspector.get_check_constraints(table_name)
                ),
                key=lambda item: (item["name"] or "", item["sqltext"]),
            ),
            "foreign_keys": sorted(
                inspector.get_foreign_keys(table_name),
                key=lambda item: item["constrained_columns"],
            ),
            "indexes": sorted(inspector.get_indexes(table_name), key=lambda item: item["name"]),
            "primary_key": inspector.get_pk_constraint(table_name),
            "unique_constraints": sorted(
                inspector.get_unique_constraints(table_name),
                key=lambda item: (item["name"] or "", item["column_names"]),
            ),
        }
        if table_name in autoincrement:
            tables[table_name]["autoincrement"] = True
    return tables


def fingerprint(specification):
    """Hash normalized SQLite reflection with CSS-01's canonical encoding."""
    return hashlib.sha256(
        json.dumps(specification, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def frozen_baseline():
    """Read the revision-owned, immutable creation recipe."""
    resource = files("scanner.persistence.migrations.versions").joinpath("baseline-v1.json")
    return json.loads(resource.read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def _baseline_specifications(recipe):
    """Verify a complete recipe before projecting its admission identities."""
    baseline = json.loads(recipe)
    if baseline["format_version"] != 1:
        raise ValueError("Unsupported baseline recipe")
    with sqlite3.connect(":memory:") as expected:
        expected.execute("PRAGMA foreign_keys=ON")
        for statement in baseline["statements"]:
            expected.execute(statement)
        expected.execute(
            "INSERT INTO filter_settings (id, revision, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            baseline["required_seeds"]["filter_settings"][0],
        )
        expected.executemany(
            "INSERT INTO mcf_severity_defaults "
            "(settings_id, subcategory, position, severity) VALUES (?, ?, ?, ?)",
            baseline["required_seeds"]["mcf_severity_defaults"],
        )
        expected.commit()
        # Reflection uses SQLAlchemy's SQLite dialect on both sides.
        engine = create_engine("sqlite://", creator=lambda: expected, poolclass=StaticPool)
        with engine.connect() as expected_connection:
            expected_spec = schema_specification(expected_connection)
            if fingerprint(expected_spec) != baseline["schema_fingerprint"]:
                raise ValueError("Packaged baseline fingerprint is inconsistent")
            expected_identities = schema_specification(expected_connection, seed_values=False)
        engine.dispose(close=False)
    return expected_identities


def _verified_baseline():
    """Key cached verification by the entire package, including editable seeds."""
    return _baseline_specifications(json.dumps(frozen_baseline(), sort_keys=True))


def _verify_seed_domains(connection, specification):
    """Evaluate trusted seed CHECKs explicitly on read-only SQLite connections."""
    mutable_columns = {"filter_settings": ("revision",), "mcf_severity_defaults": ("severity",)}
    for table_name, names in mutable_columns.items():
        table = specification["tables"][table_name]
        predicates = [f"NOT ({check['sqltext']})" for check in table["checks"]]
        for column in table["columns"]:
            if column["name"] not in names:
                continue
            name = column["name"]
            if not column["nullable"]:
                predicates.append(f'"{name}" IS NULL')
            if column["type"] == "INTEGER":
                predicates.append(f"typeof(\"{name}\") != 'integer'")
        if connection.execute(
            text(f'SELECT 1 FROM "{table_name}" WHERE {" OR ".join(predicates)} LIMIT 1')
        ).first():
            raise ValueError("Unsupported mutable seed value domain")


def verify_frozen_baseline(connection):
    """Require a verified package, exact identities, and valid editable values."""
    expected_identities = _verified_baseline()
    if schema_specification(connection, seed_values=False) != expected_identities:
        raise ValueError("Unsupported unversioned database schema or required seeds")
    _verify_seed_domains(connection, expected_identities)


@contextmanager
def migrated_in_memory(revision):
    """Yield a connection to a disposable database the packaged migrations built to `revision`."""
    from scanner.persistence.migrations.runner import upgrade_connection

    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            upgrade_connection(connection, revision)
            yield connection
    finally:
        engine.dispose()


@lru_cache(maxsize=4)
def _versioned_specification(revision):
    """Build a packaged revision's shape in disposable memory."""
    _verified_baseline()
    with migrated_in_memory(revision) as connection:
        return schema_specification(connection, versioned=True, seed_values=False)


# Column names a revision's published earlier shape used; the next revision renames them.
_EARLIER_COLUMN_NAMES = {
    "0039_review_community_sources": {
        ("community_master_preferences", "hide_downvoted"): "hide_rejected_downvoted",
    },
}


def _earlier_specification(expected, revision):
    """Return the revision's earlier published shape, or None when it has none."""
    renames = _EARLIER_COLUMN_NAMES.get(revision)
    if not renames:
        return None
    earlier = json.loads(json.dumps(expected))
    for (table, name), old_name in renames.items():
        for column in earlier["tables"][table]["columns"]:
            if column["name"] == name:
                column["name"] = old_name
    return earlier


def verify_versioned_schema(connection, revision):
    """Reject modified schemas at a known packaged revision.

    A revision whose published shape later changed in place also accepts that earlier
    shape; the following migration converts it.
    """
    _verified_baseline()
    expected = _versioned_specification(revision)
    actual = schema_specification(connection, versioned=True, seed_values=False)
    if actual != expected and actual != _earlier_specification(expected, revision):
        raise ValueError("Unsupported versioned database schema or required seeds")
    _verify_seed_domains(connection, expected)


def verify_current_schema(connection):
    """Reject mutated current schemas without inspecting private user rows."""
    from scanner.persistence.migrations.adoption import HEAD_REVISION

    verify_versioned_schema(connection, HEAD_REVISION)
