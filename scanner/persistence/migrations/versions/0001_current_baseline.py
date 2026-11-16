"""Frozen CSS-01 current schema baseline.

Revision ID: 0001_current_baseline
Revises:
"""

import json
from pathlib import Path

from alembic import op

revision = "0001_current_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Build the frozen schema and seed values without importing mutable ORM metadata."""
    resource = Path(__file__).with_name("baseline-v1.json")
    baseline = json.loads(resource.read_text(encoding="utf-8"))
    if baseline["format_version"] != 1:
        raise RuntimeError("Unsupported baseline migration resource")
    connection = op.get_bind()
    for statement in baseline["statements"]:
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql(
        "INSERT INTO filter_settings (id, revision, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        tuple(baseline["required_seeds"]["filter_settings"][0]),
    )
    connection.exec_driver_sql(
        "INSERT INTO mcf_severity_defaults "
        "(settings_id, subcategory, position, severity) VALUES (?, ?, ?, ?)",
        [tuple(row) for row in baseline["required_seeds"]["mcf_severity_defaults"]],
    )


def downgrade():
    """A destructive baseline rollback is not a supported operation."""
    raise RuntimeError("Local database migrations cannot discard the baseline")
