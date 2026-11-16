"""Store owner-granted exceptions for media that matches no Jellyfin item.

Adds one empty table; no existing row changes. Local defaults and individual
selections already live in `media_access_scopes` from revision 0011.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_media_access_exceptions"
down_revision = "0013_community_identities"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "media_access_exceptions",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_kind", sa.String(16), primary_key=True),
        sa.Column("server_key", sa.String(), primary_key=True),
        sa.Column("item_key", sa.String(), primary_key=True),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('jellyfin_library', 'radarr', 'sonarr', 'manual_files')",
            name="mediasourcekind",
        ),
        sa.CheckConstraint(
            "source_kind != 'jellyfin_library'", name="ck_media_access_exceptions_source_kind"
        ),
        sa.CheckConstraint(
            "length(server_key) > 0 AND length(item_key) > 0",
            name="ck_media_access_exceptions_key",
        ),
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard media access exceptions")
