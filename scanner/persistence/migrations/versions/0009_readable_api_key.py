"""Retain API keys for administrator viewing without replacing existing credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0009_readable_api_key"
down_revision = "0008_api_key"
branch_labels = None
depends_on = None


def upgrade():
    # Earlier digest-only credentials remain valid; their original value is unknowable.
    op.add_column("api_keys", sa.Column("token_value", sa.Text(), nullable=True))


def downgrade():
    raise RuntimeError("Local migrations cannot discard stored API credentials")
