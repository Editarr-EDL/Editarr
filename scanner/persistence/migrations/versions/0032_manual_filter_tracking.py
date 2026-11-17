"""Record hand-added and hand-edited filter provenance when it is known."""

import sqlalchemy as sa
from alembic import op

revision = "0032_manual_filter_tracking"
down_revision = "0031_explicit_filter_modes"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("scan_findings", "review_filters"):
        op.add_column(table, sa.Column("manually_added", sa.Boolean(), nullable=True))
        op.add_column(table, sa.Column("manually_edited", sa.Boolean(), nullable=True))


def downgrade():
    raise RuntimeError("Local migrations cannot discard recorded manual filter provenance")
