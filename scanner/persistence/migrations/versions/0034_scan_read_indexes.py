"""Index scan finding order and reverse scan associations for history and review reads."""

from alembic import op

revision = "0034_scan_read_indexes"
down_revision = "0033_scan_worker_timing"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_scan_findings_scan_start", "scan_findings", ["scan_id", "start_seconds", "id"]
    )
    op.create_index(
        "ix_scan_media_item_associations_media_item_id",
        "scan_media_item_associations",
        ["media_item_id"],
    )


def downgrade():
    op.drop_index(
        "ix_scan_media_item_associations_media_item_id", table_name="scan_media_item_associations"
    )
    op.drop_index("ix_scan_findings_scan_start", table_name="scan_findings")
