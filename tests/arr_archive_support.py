"""Return a head database to its 0024 shape, for tests that rebuild an older schema by hand."""

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_OLD_SOURCE_KINDS = "source_kind IN ('jellyfin_library', 'radarr', 'sonarr', 'manual_files')"


def undo_archive_migration(connection) -> None:
    """Reverse the shapes of 0025 and 0026 on a SQLAlchemy connection with foreign keys off."""
    operations = Operations(MigrationContext.configure(connection))
    with operations.batch_alter_table("media_items", recreate="always") as batch:
        batch.drop_constraint("archivedsource", type_="check")
        batch.drop_column("archived_source")
    with operations.batch_alter_table("arr_settings", recreate="always") as batch:
        batch.add_column(sa.Column("api_key", sa.Text(), nullable=True), insert_after="server_url")
    with operations.batch_alter_table("media_access_scope_sources", recreate="always") as batch:
        batch.drop_constraint("mediasourcekind", type_="check")
        batch.create_check_constraint("mediasourcekind", _OLD_SOURCE_KINDS)
    operations.create_table(
        "media_access_exceptions",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("source_kind", sa.String(16), primary_key=True),
        sa.Column("server_key", sa.String(), primary_key=True),
        sa.Column("item_key", sa.String(), primary_key=True),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(_OLD_SOURCE_KINDS, name="mediasourcekind"),
        sa.CheckConstraint(
            "source_kind != 'jellyfin_library'", name="ck_media_access_exceptions_source_kind"
        ),
        sa.CheckConstraint(
            "length(server_key) > 0 AND length(item_key) > 0",
            name="ck_media_access_exceptions_key",
        ),
    )
    operations.drop_table("archived_media_access_rows")
