"""Remember the Jellyfin users the owner keeps out after deleting their accounts.

`jellyfin_user_blocks` gets one row per blocked Jellyfin server and user ID, with
Jellyfin's name for the user, who blocked them, and when. It references no account
but the blocker, so a block outlives the deleted account. Existing rows are untouched.
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_jellyfin_user_blocks"
down_revision = "0028_jellyfin_admission"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jellyfin_user_blocks",
        sa.Column("server_id", sa.String(), nullable=False),
        sa.Column("provider_user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("blocked_by", sa.Integer(), nullable=True),
        sa.Column("blocked_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(server_id) > 0", name="ck_jellyfin_user_blocks_server_id"),
        sa.CheckConstraint(
            "length(provider_user_id) = 32 AND provider_user_id NOT GLOB '*[^0-9a-f]*' "
            "AND provider_user_id != '00000000000000000000000000000000'",
            name="ck_jellyfin_user_blocks_user_id",
        ),
        sa.CheckConstraint(
            "name IS NULL OR length(name) BETWEEN 1 AND 256", name="ck_jellyfin_user_blocks_name"
        ),
        sa.ForeignKeyConstraint(["blocked_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("server_id", "provider_user_id"),
    )


def downgrade():
    raise RuntimeError("Jellyfin user blocks require backup recovery, not a downgrade")
