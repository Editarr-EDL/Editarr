"""Read 0037 databases in the shape of earlier revisions, for upgrade comparisons.

0037 renames `community_imports` and `community_import_targets` with a `retired_`
prefix, moves receipt and vote operations verbatim to `retired_community_operations`,
and adds empty master-set tables. `tests/test_community_master_migration.py` pins
those moves, and that unfinished publications end `failed` with `protocol_retired`.
"""

from __future__ import annotations

import sqlite3

MASTER_TABLES = (
    "community_master_links",
    "community_master_snapshots",
    "community_master_filters",
    "community_master_updates",
    "community_master_dismissals",
    "community_master_preferences",
    "community_sync_state",
    "community_sync_notifications",
    # 0039: saved reviews' community master links.
    "review_community_sources",
)
RETIRED_TABLES = {
    "retired_community_imports": "community_imports",
    "retired_community_import_targets": "community_import_targets",
}
RETIRED_OPERATIONS = "retired_community_operations"
# Columns 0037 adds; snapshots ignore them on both sides.
MASTER_COLUMNS = {
    "community_operations": {"community_version_id", "filter_id", "receipt_id"},
    RETIRED_OPERATIONS: {"retired_at", "retired_reason"},
}


def put_back_retired(rows: dict, columns: dict) -> None:
    """Put retired rows back under their pre-0037 table names, in place.

    Master-set tables are dropped only while empty. Operations are sorted, because
    retired ones leave their original row order.
    """
    for table in MASTER_TABLES:
        if table in rows and not rows[table]:
            rows.pop(table)
            columns.pop(table)
    for retired, source in RETIRED_TABLES.items():
        if retired in rows:
            rows[source], columns[source] = rows.pop(retired), columns.pop(retired)
    if RETIRED_OPERATIONS in rows:
        assert columns.pop(RETIRED_OPERATIONS) == columns["community_operations"]
        rows["community_operations"] += rows.pop(RETIRED_OPERATIONS)
    if "community_operations" in rows:
        rows["community_operations"].sort(key=repr)


def legacy_query(name: str, selected: str) -> str:
    """SQL reading a pre-0037 table's `selected` columns from either schema, comparably."""
    if name == "community_operations":
        return (
            f"SELECT {selected} FROM community_operations UNION ALL "
            f"SELECT {selected} FROM {RETIRED_OPERATIONS} ORDER BY operation_id"
        )
    source = {value: key for key, value in RETIRED_TABLES.items()}.get(name)
    return f'SELECT {selected} FROM "{source or name}" ORDER BY rowid'


def legacy_rows(database: sqlite3.Connection, name: str, names: list[str]) -> list[tuple]:
    """Rows of `names` from `name` or its 0037 successor, operations in ID order."""
    if name == "community_operations" and "operation_id" not in names:
        names = ["operation_id", *names]
        return [row[1:] for row in legacy_rows(database, name, names)]
    selected = ", ".join(f'"{column}"' for column in names)
    has_retired = database.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ?", (RETIRED_OPERATIONS,)
    ).fetchone()
    if not has_retired:
        if name == "community_operations":
            return database.execute(
                f"SELECT {selected} FROM community_operations ORDER BY operation_id"
            ).fetchall()
        return database.execute(f'SELECT {selected} FROM "{name}" ORDER BY rowid').fetchall()
    return database.execute(legacy_query(name, selected)).fetchall()


def comparable(name: str, names: list[str], rows: list[tuple]) -> list[tuple]:
    """Order expected rows as `legacy_rows` reads them: operations by ID."""
    if name != "community_operations":
        return list(rows)
    if "operation_id" not in names:
        raise ValueError("Operation comparisons need operation_id")
    return sorted(rows, key=lambda row: row[names.index("operation_id")])


_UPDATED_AT, _ERROR_CODE = 13, 12


def _replace(row, index, value):
    return (*row[:index], value, *row[index + 1 :]) if index is not None else row


def retire_publications(original: dict, upgraded: dict, columns: dict | None = None) -> dict:
    """The rows `original` holds after 0037, given the `upgraded` rows.

    Unfinished publish operations end `failed` at the upgrade's time, and their
    publications record `protocol_retired`; every other row is unchanged. Rows use
    full legacy column order unless `columns` names each table's selected columns.
    """

    def index(table, name, default):
        if columns is None:
            return default
        names = columns.get(table, ())
        return names.index(name) if name in names else None

    kind = index("community_operations", "kind", 2)
    state = index("community_operations", "state", 3)
    updated_at = index("community_operations", "updated_at", _UPDATED_AT)
    error_code = index("community_publications", "error_code", _ERROR_CODE)
    expected = dict(original)
    after = {row[0]: row for row in upgraded.get("community_operations", [])}
    retired, operations = set(), []
    for row in original.get("community_operations", []):
        if (
            kind is not None
            and state is not None
            and row[kind] == "publish"
            and (row[state] in ("pending", "submitted"))
        ):
            retired.add(row[0])
            row = _replace(
                _replace(row, state, "failed"),
                updated_at,
                after[row[0]][updated_at] if updated_at is not None else None,
            )
        operations.append(row)
    if "community_operations" in original:
        expected["community_operations"] = operations
    if "community_publications" in original:
        expected["community_publications"] = [
            _replace(row, error_code, "protocol_retired") if row[0] in retired else row
            for row in original["community_publications"]
        ]
    return expected
