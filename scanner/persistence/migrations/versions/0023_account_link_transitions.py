"""Record account-link transitions and the personal export files each one moves."""

import sqlalchemy as sa
from alembic import op

revision = "0023_account_link_transitions"
down_revision = "0022_export_regenerations"
branch_labels = None
depends_on = None

_HEX = "NOT GLOB '*[^0-9a-f]*'"


def _export_id(column):
    return (
        f"(length({column}) = 38 AND substr({column}, 1, 6) = 'local-' "
        f"AND substr({column}, 7) {_HEX}) OR "
        f"(length({column}) = 32 AND {column} {_HEX} "
        f"AND {column} != '00000000000000000000000000000000')"
    )


def upgrade():
    op.create_table(
        "account_link_transitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("from_server_id", sa.String(), nullable=True),
        sa.Column("from_provider_user_id", sa.String(), nullable=True),
        sa.Column("to_server_id", sa.String(), nullable=False),
        sa.Column("to_provider_user_id", sa.String(), nullable=False),
        sa.Column("from_export_id", sa.String(), nullable=False),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('link', 'relink')", name="ck_account_link_transitions_kind"),
        sa.CheckConstraint(
            "state IN ('writing', 'removing', 'completed', 'rolled_back')",
            name="ck_account_link_transitions_state",
        ),
        sa.CheckConstraint(
            "(kind = 'link') = (from_server_id IS NULL) "
            "AND (from_server_id IS NULL) = (from_provider_user_id IS NULL)",
            name="ck_account_link_transitions_from",
        ),
        sa.CheckConstraint(
            f"length(to_server_id) > 0 AND length(to_provider_user_id) = 32 "
            f"AND to_provider_user_id {_HEX} "
            f"AND to_provider_user_id != '00000000000000000000000000000000'",
            name="ck_account_link_transitions_to",
        ),
        sa.CheckConstraint(
            f"({_export_id('from_export_id')}) "
            "AND (kind = 'link') = (substr(from_export_id, 1, 6) = 'local-') "
            "AND (kind = 'link' OR from_export_id = from_provider_user_id)",
            name="ck_account_link_transitions_from_export_id",
        ),
        sa.CheckConstraint(
            "(state = 'rolled_back') = (failure_reason IS NOT NULL) "
            "AND (failure_reason IS NULL "
            "OR failure_reason IN ('write_failed', 'refused', 'interrupted'))",
            name="ck_account_link_transitions_failure",
        ),
    )
    op.create_index(
        "uq_account_link_transitions_open",
        "account_link_transitions",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("state IN ('writing', 'removing')"),
    )
    op.create_table(
        "account_link_export_moves",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "transition_id",
            sa.Integer(),
            sa.ForeignKey("account_link_transitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        sa.CheckConstraint("old_path != new_path", name="ck_account_link_export_moves_paths"),
        sa.CheckConstraint(
            "(old_sha256 IS NULL OR (length(old_sha256) = 64 AND old_sha256 "
            f"{_HEX})) AND (new_sha256 IS NULL OR (length(new_sha256) = 64 "
            f"AND new_sha256 {_HEX}))",
            name="ck_account_link_export_moves_digests",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('removed', 'absent', 'kept')",
            name="ck_account_link_export_moves_outcome",
        ),
        sa.CheckConstraint("requeued IN (0, 1)", name="ck_account_link_export_moves_requeued"),
        sa.CheckConstraint(
            "new_file_id IS NULL OR new_sha256 IS NOT NULL",
            name="ck_account_link_export_moves_new_file",
        ),
    )
    op.create_index(
        "ix_account_link_export_moves_transition",
        "account_link_export_moves",
        ["transition_id"],
    )


def downgrade():
    raise RuntimeError("Account-link transitions require backup recovery, not a downgrade")
