"""Preserve review references lost while historical SQLite FKs were disabled."""

import base64
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0020_review_scan_repairs"
down_revision = "0019_review_policy_evidence"
branch_labels = None
depends_on = None

_SCAN_COLUMNS = {
    "scan_findings": "scan_id",
    "scan_queue": "scan_id",
    "scan_media_item_associations": "scan_id",
    "scan_stage_coverage": "scan_id",
    "credits_detection_runs": "scan_id",
    "scan_mcf_documents": "scan_id",
    "community_imports": "scan_id",
    "community_operations": "scan_id",
    "review_scan_memberships": "scan_id",
    "review_filters": "source_scan_id",
    "review_accepted_credits": "scan_id",
    "media_item_export_scans": "scan_id",
}


def _rows(connection, table):
    return [dict(row) for row in connection.execute(sa.text(f'SELECT * FROM "{table}"')).mappings()]


def _json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda item: {"$bytes": base64.b64encode(item).decode("ascii")},
    )


def _delete(connection, table, rows):
    primary = [
        row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")') if row[5]
    ]
    predicate = " AND ".join(f'"{column}" = :{column}' for column in primary)
    for row in rows:
        connection.execute(sa.text(f'DELETE FROM "{table}" WHERE {predicate}'), row)


def upgrade():
    connection = op.get_bind()
    unattributed = connection.execute(
        sa.text(
            "SELECT link.media_item_export_id, link.scan_id "
            "FROM media_item_export_scans link "
            "JOIN media_item_exports export ON export.id = link.media_item_export_id "
            "LEFT JOIN scans scan ON scan.id = link.scan_id "
            "WHERE scan.id IS NULL AND export.review_id IS NULL LIMIT 1"
        )
    ).first()
    if unattributed is not None:
        raise ValueError(
            "Unattributed export scan provenance cannot be repaired: "
            f"export={unattributed[0]}, missing_scan={unattributed[1]}"
        )
    op.create_table(
        "review_scan_repairs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "review_id",
            sa.String(),
            sa.ForeignKey("saved_reviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("missing_scan_id", sa.String(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("preserved_json", sa.Text(), nullable=False),
        sa.Column("community_json", sa.Text(), nullable=True),
        sa.Column("community_unavailable_reason", sa.String(), nullable=True),
        sa.UniqueConstraint("review_id", "missing_scan_id", name="uq_review_scan_repairs_scan"),
    )
    scans = {row["id"] for row in _rows(connection, "scans")}
    tables = {table: _rows(connection, table) for table in _SCAN_COLUMNS}
    missing = {
        row[column]
        for table, column in _SCAN_COLUMNS.items()
        for row in tables[table]
        if row[column] is not None and row[column] not in scans
    }
    exports = {row["id"]: row["review_id"] for row in _rows(connection, "media_item_exports")}
    targets = _rows(connection, "community_import_targets")
    evidence = _rows(connection, "review_policy_evidence")
    reviews = {row["id"] for row in _rows(connection, "saved_reviews")}
    detected_at = datetime.now(timezone.utc).isoformat()
    for scan_id in sorted(missing):
        source_findings = [row for row in tables["scan_findings"] if row["scan_id"] == scan_id]
        finding_ids = {row["id"] for row in source_findings}
        removed = {
            table: [row for row in rows if row[_SCAN_COLUMNS[table]] == scan_id]
            for table, rows in tables.items()
        }
        for table in ("review_filters", "review_accepted_credits"):
            removed[table].extend(
                row
                for row in tables[table]
                if row["source_finding_id"] in finding_ids
                and row[_SCAN_COLUMNS[table]] not in missing
            )
        finding_ids.update(
            row["source_finding_id"]
            for table in ("review_filters", "review_accepted_credits")
            for row in removed[table]
            if row["source_finding_id"] is not None
        )
        removed["review_policy_evidence"] = [
            row for row in evidence if row["source_finding_id"] in finding_ids
        ]
        import_ids = {row["id"] for row in removed["community_imports"]}
        removed["community_import_targets"] = [
            row for row in targets if row["import_id"] in import_ids
        ]
        affected = {
            row["review_id"]
            for table in (
                "review_scan_memberships",
                "review_filters",
                "review_accepted_credits",
                "review_policy_evidence",
            )
            for row in removed[table]
        } | {exports[row["media_item_export_id"]] for row in removed["media_item_export_scans"]}
        community = next(iter(removed["community_imports"]), None)
        if community is not None:
            community = {
                key: community[key]
                for key in (
                    "service_url",
                    "contribution_id",
                    "revision_id",
                    "digest",
                    "artifact_type",
                    "event_ids_json",
                    "receipt_json",
                )
            }
        elif removed["community_operations"]:
            operation = removed["community_operations"][0]
            community = {
                key: operation[key]
                for key in (
                    "service_url",
                    "contribution_id",
                    "revision_id",
                    "digest",
                )
            }
        for review_id in sorted(affected & reviews):
            preserved = {}
            for table, rows in removed.items():
                if table.startswith("review_"):
                    rows = [row for row in rows if row["review_id"] == review_id]
                elif table == "media_item_export_scans":
                    rows = [
                        row for row in rows if exports[row["media_item_export_id"]] == review_id
                    ]
                preserved[table] = rows
            connection.execute(
                sa.text(
                    "INSERT INTO review_scan_repairs "
                    "(review_id, missing_scan_id, detected_at, preserved_json, community_json) "
                    "VALUES (:review, :scan, :detected, :preserved, :community)"
                ),
                {
                    "review": review_id,
                    "scan": scan_id,
                    "detected": detected_at,
                    "preserved": _json(preserved),
                    "community": _json(community) if community else None,
                },
            )
        for table in (
            "review_policy_evidence",
            "review_accepted_credits",
            "review_filters",
            "review_scan_memberships",
            "media_item_export_scans",
            "community_import_targets",
            "community_imports",
        ):
            _delete(connection, table, removed[table])
        connection.execute(
            sa.text("UPDATE community_operations SET scan_id = NULL WHERE scan_id = :scan"),
            {"scan": scan_id},
        )
        for table in (
            "scan_findings",
            "scan_queue",
            "scan_media_item_associations",
            "scan_stage_coverage",
            "credits_detection_runs",
            "scan_mcf_documents",
        ):
            _delete(connection, table, removed[table])
    connection.execute(
        sa.text(
            "DELETE FROM community_import_targets "
            "WHERE import_id NOT IN (SELECT id FROM community_imports)"
        )
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard preserved review repairs")
