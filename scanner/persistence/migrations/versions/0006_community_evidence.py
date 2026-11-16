"""Retain original verified community bytes and recoverable post-import receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0006_community_evidence"
down_revision = "0005_audio_tracks"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("artifact_bytes", "manifest_bytes", "signature_bytes"):
        op.add_column("community_imports", sa.Column(name, sa.LargeBinary(), nullable=True))
    for name in ("historical_key_json", "provenance_json", "event_ids_json", "receipt_json"):
        op.add_column("community_imports", sa.Column(name, sa.Text(), nullable=True))
    op.create_table(
        "community_import_targets",
        sa.Column(
            "import_id",
            sa.String(36),
            sa.ForeignKey("community_imports.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "media_item_id",
            sa.String(),
            sa.ForeignKey("media_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("media_source_id", sa.String(), primary_key=True),
        sa.Column("resolved_local_path", sa.String(), nullable=False),
        sa.Column("video_hash", sa.String(), nullable=True),
        sa.Column("target_evidence_json", sa.Text(), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard signed community evidence")
