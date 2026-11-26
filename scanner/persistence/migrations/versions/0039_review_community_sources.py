"""Store community filter snapshots in saved reviews, and a master-list display preference.

- `review_community_sources` holds each saved review's included community filters as a
  snapshot of a master link's active filters, taken when the review included or last
  refreshed them. The snapshot outlives its link: `link_id` becomes null when the link
  is deleted, and the review keeps the filters.
- `community_master_preferences.hide_downvoted` (default false) hides downvoted rows
  from the account's master filter list.

No existing row changes. The adoption path runs this only after its verified,
recoverable backup.

(0040 later made each snapshot filter an editable `review_filters` row; since then
the snapshot only records master values and starting states for Refresh and sharing.)
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_review_community_sources"
down_revision = "0038_retire_receipt_capabilities"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_community_sources",
        sa.Column(
            "review_id",
            sa.String(),
            sa.ForeignKey("saved_reviews.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "link_id", sa.String(36), sa.ForeignKey("community_master_links.id"), nullable=True
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("community_version_id", sa.String(128), nullable=False),
        sa.Column("label", sa.String(256), nullable=False),
        sa.Column("tier", sa.String(8), nullable=True),
        sa.Column("match_percent", sa.Integer(), nullable=True),
        sa.Column("master_revision", sa.Integer(), nullable=False),
        sa.Column("alignment_json", sa.Text(), nullable=True),
        sa.Column("filters_json", sa.Text(), nullable=False),
        sa.Column("filters_digest", sa.String(64), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_review_community_sources_position"),
        sa.CheckConstraint(
            "master_revision >= 1", name="ck_review_community_sources_master_revision"
        ),
    )
    op.create_index("ix_review_community_sources_link", "review_community_sources", ["link_id"])
    with op.batch_alter_table("community_master_preferences") as batch:
        batch.add_column(
            sa.Column(
                "hide_downvoted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade():
    """Undo only when nothing would be lost: no snapshot and no hide preference set."""
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT 1 FROM review_community_sources LIMIT 1").first():
        raise RuntimeError("Restore a backup to downgrade without discarding review snapshots")
    if bind.exec_driver_sql(
        "SELECT 1 FROM community_master_preferences WHERE hide_downvoted LIMIT 1"
    ).first():
        raise RuntimeError("Restore a backup to downgrade without discarding preferences")
    with op.batch_alter_table("community_master_preferences") as batch:
        batch.drop_column("hide_downvoted")
    op.drop_index("ix_review_community_sources_link", table_name="review_community_sources")
    op.drop_table("review_community_sources")
