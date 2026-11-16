"""Preserve explicitly confirmed local media identity.

Revision ID: 0004_media_identity
Revises: 0003_local_community
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_media_identity"
down_revision = "0003_local_community"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media_items", sa.Column("confirmed_identity_json", sa.Text(), nullable=True))


def downgrade():
    raise RuntimeError("Local migrations cannot discard confirmed media identity")
