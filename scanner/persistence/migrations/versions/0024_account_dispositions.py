"""Record deleted accounts, transferred export moves, and intended policy owners."""

import sqlalchemy as sa
from alembic import op

revision = "0024_account_dispositions"
down_revision = "0023_account_link_transitions"
branch_labels = None
depends_on = None

_HEX = "NOT GLOB '*[^0-9a-f]*'"


def upgrade():
    op.create_table(
        "account_dispositions",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("destination_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmation", sa.String(), nullable=False),
        sa.Column("impact_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "mode IN ('retain', 'transfer', 'delete')", name="ck_account_dispositions_mode"
        ),
        sa.CheckConstraint(
            "(mode = 'transfer') = (destination_id IS NOT NULL) "
            "AND (destination_id IS NULL OR destination_id != user_id)",
            name="ck_account_dispositions_destination",
        ),
        sa.CheckConstraint(
            "length(confirmation) = 64 AND confirmation NOT GLOB '*[^0-9a-f]*'",
            name="ck_account_dispositions_confirmation",
        ),
    )
    op.create_table(
        "account_transfer_moves",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("confirmation", sa.String(), nullable=False),
        sa.Column(
            "export_id",
            sa.Integer(),
            sa.ForeignKey("media_item_exports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("old_path", sa.Text(), nullable=False),
        sa.Column("new_path", sa.Text(), nullable=False),
        sa.Column("old_sha256", sa.String(), nullable=True),
        sa.Column("new_sha256", sa.String(), nullable=True),
        sa.Column("new_file_id", sa.String(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("requeued", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("old_path != new_path", name="ck_account_transfer_moves_paths"),
        sa.CheckConstraint(
            f"(old_sha256 IS NULL OR (length(old_sha256) = 64 AND old_sha256 {_HEX})) "
            f"AND (new_sha256 IS NULL OR (length(new_sha256) = 64 AND new_sha256 {_HEX}))",
            name="ck_account_transfer_moves_digests",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('removed', 'absent', 'kept')",
            name="ck_account_transfer_moves_outcome",
        ),
        sa.CheckConstraint("requeued IN (0, 1)", name="ck_account_transfer_moves_requeued"),
        sa.CheckConstraint(
            f"length(confirmation) = 64 AND confirmation {_HEX}",
            name="ck_account_transfer_moves_confirmation",
        ),
        sa.CheckConstraint(
            "new_file_id IS NULL OR new_sha256 IS NOT NULL",
            name="ck_account_transfer_moves_new_file",
        ),
    )
    op.create_index("ix_account_transfer_moves_user", "account_transfer_moves", ["user_id"])
    op.add_column(
        "media_item_exports",
        sa.Column("intended_policy_owner_id", sa.Integer(), nullable=True),
    )


def downgrade():
    raise RuntimeError("Account dispositions require backup recovery, not a downgrade")
