"""Durable local community metadata and retry operations.

Revision ID: 0003_local_community
Revises: 0002_scan_provenance
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_local_community"
down_revision = "0002_scan_provenance"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "community_connections",
        sa.Column("service_url", sa.String(256), primary_key=True),
        sa.Column("contributor_id", sa.String(128), nullable=True),
        sa.Column("installation_id", sa.String(36), nullable=True),
        sa.Column("key_id", sa.String(71), nullable=True),
        sa.Column("sharing_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("consent_version", sa.String(128), nullable=True),
        sa.Column("consented_at", sa.String(64), nullable=True),
        sa.CheckConstraint("length(service_url) > 0", name="ck_community_connection_service"),
        sa.CheckConstraint("sharing_enabled IN (0, 1)", name="ck_community_connection_sharing"),
    )
    op.create_table(
        "community_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scan_id",
            sa.String(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("service_url", sa.String(256), nullable=False),
        sa.Column("contribution_id", sa.String(128), nullable=False),
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("digest", sa.String(71), nullable=False),
        sa.Column("author_id", sa.String(128), nullable=False),
        sa.Column("artifact_type", sa.String(32), nullable=False),
        sa.UniqueConstraint(
            "service_url", "revision_id", "digest", name="uq_community_import_revision"
        ),
    )
    op.create_table(
        "community_operations",
        sa.Column("operation_id", sa.String(128), primary_key=True),
        sa.Column("service_url", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("contributor_id", sa.String(128), nullable=True),
        sa.Column("installation_id", sa.String(36), nullable=True),
        sa.Column("contribution_id", sa.String(128), nullable=True),
        sa.Column("revision_id", sa.String(128), nullable=True),
        sa.Column("digest", sa.String(71), nullable=True),
        sa.Column(
            "scan_id", sa.String(), sa.ForeignKey("scans.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("consent_version", sa.String(128), nullable=True),
        sa.Column("receipt", sa.String(2048), nullable=True),
        sa.Column("vote", sa.String(16), nullable=True),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "kind IN ('publish', 'receipt', 'vote')", name="ck_community_operation_kind"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'submitted', 'acknowledged', 'failed')",
            name="ck_community_operation_state",
        ),
        sa.Index("ix_community_operation_state", "kind", "state"),
    )


def downgrade():
    raise RuntimeError("Local database migrations cannot discard community state")
