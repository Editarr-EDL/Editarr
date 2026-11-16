"""Add a revocable local API credential without changing existing data."""

import sqlalchemy as sa
from alembic import op

revision = "0008_api_key"
down_revision = "0007_community_publications"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_digest", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_api_keys_singleton_id"),
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard API credentials")
