"""Retain exact publication content and provenance independently of saved reviews."""

import sqlalchemy as sa
from alembic import op

revision = "0021_export_snapshots"
down_revision = "0020_review_scan_repairs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media_item_exports", sa.Column("content_bytes", sa.LargeBinary(), nullable=True))
    op.add_column("media_item_exports", sa.Column("snapshot_json", sa.Text(), nullable=True))
    op.add_column("media_item_exports", sa.Column("destination_path", sa.String(), nullable=True))
    op.add_column(
        "media_item_exports",
        sa.Column("publication_revision", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    raise RuntimeError("Export snapshots require backup recovery, not a destructive downgrade")
