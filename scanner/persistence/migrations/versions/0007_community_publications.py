"""Preserve exact approved publication payloads across restart and restore."""

import sqlalchemy as sa
from alembic import op

revision = "0007_community_publications"
down_revision = "0006_community_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "community_publications",
        sa.Column(
            "operation_id",
            sa.String(128),
            sa.ForeignKey("community_operations.operation_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("artifact_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("manifest_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("signature_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("signature_digest", sa.String(64), nullable=False),
        sa.Column("preview_digest", sa.String(64), nullable=False),
        sa.Column("consent_token", sa.String(64), nullable=False),
        sa.Column("author_key_id", sa.String(71), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard approved publication evidence")
