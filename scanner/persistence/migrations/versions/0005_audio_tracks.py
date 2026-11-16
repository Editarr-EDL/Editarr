"""Preserve requested and actually analyzed local audio track snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0005_audio_tracks"
down_revision = "0004_media_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scans", sa.Column("requested_audio_selection_json", sa.Text(), nullable=True))
    op.add_column("scans", sa.Column("analyzed_audio_selection_json", sa.Text(), nullable=True))
    op.create_table(
        "audio_track_selections",
        sa.Column("file_path", sa.String(), primary_key=True),
        sa.Column("selection_json", sa.Text(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard analyzed audio provenance")
