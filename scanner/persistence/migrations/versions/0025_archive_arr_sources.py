"""Archive Radarr and Sonarr records now that Editarr reads only Jellyfin and Other Files.

Every row stays. A media item from either source gets `archived_source`, and its
scans, reviews, and exports become read-only through it. Unfinished work on those
items stops the way a cancellation or a failed regeneration does. Their unsent
community publications end cancelled, and their submitted ones await the server's
answer with a cancel request. Connection rows
keep their URL and lose their API key. Media access grants for either source move
to `archived_media_access_rows` as the JSON of the original row.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0025_archive_arr_sources"
down_revision = "0024_account_dispositions"
branch_labels = None
depends_on = None

_SOURCES = ("radarr", "sonarr")
_ARCHIVED_ITEMS = "SELECT id FROM media_items WHERE archived_source IS NOT NULL"
_ARCHIVED_SCANS = f"SELECT id FROM scans WHERE media_item_id IN ({_ARCHIVED_ITEMS})"
_ARCHIVED_REVIEWS = f"SELECT id FROM saved_reviews WHERE media_item_id IN ({_ARCHIVED_ITEMS})"
_TERMINAL_SCANS = "('complete', 'error', 'failed', 'cancelled')"
# A publish operation of archived work; its source, like its scan, can no longer be read.
_ARCHIVED_PUBLICATIONS = (
    "SELECT operation_id FROM community_operations WHERE kind = 'publish' "
    "AND state IN ('pending', 'submitted') AND (scan_id IN ({scans}) OR operation_id IN "
    "(SELECT operation_id FROM community_publications WHERE "
    "(source_kind = 'scan' AND source_id IN ({scans})) OR "
    "(source_kind = 'review' AND source_id IN ({reviews}))))"
).format(scans=_ARCHIVED_SCANS, reviews=_ARCHIVED_REVIEWS)
_ACCESS_TABLES = ("media_access_scope_sources", "media_access_exceptions")


def _archive_media_items(bind):
    with op.batch_alter_table("media_items", recreate="always") as batch:
        batch.add_column(sa.Column("archived_source", sa.String(6), nullable=True))
        batch.create_check_constraint("archivedsource", "archived_source IN ('radarr', 'sonarr')")
    for source in _SOURCES:
        bind.execute(
            sa.text(
                "UPDATE media_items SET archived_source = :source "
                "WHERE substr(server_id, 1, length(:prefix)) = :prefix"
            ),
            {"source": source, "prefix": f"{source}:"},
        )


def _cancel_archived_publications(bind):
    """Request cancellation of each unfinished publication and end the unsent ones.

    An unsent one ends `failed`, which with the request reads as cancelled. A submitted
    one may already exist on the server, so it stays `submitted` and a check still
    learns its outcome.
    """
    bind.execute(
        sa.text(
            "UPDATE community_publications SET cancel_requested = 1 "
            f"WHERE operation_id IN ({_ARCHIVED_PUBLICATIONS})"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE community_operations SET state = 'failed' "
            f"WHERE state = 'pending' AND operation_id IN ({_ARCHIVED_PUBLICATIONS})"
        )
    )


def _stop_archived_work(bind):
    _cancel_archived_publications(bind)
    bind.execute(
        sa.text(
            f"UPDATE scans SET status = 'cancelled' WHERE id IN ({_ARCHIVED_SCANS}) "
            f"AND status NOT IN {_TERMINAL_SCANS}"
        )
    )
    bind.execute(sa.text(f"DELETE FROM scan_queue WHERE scan_id IN ({_ARCHIVED_SCANS})"))
    bind.execute(
        sa.text(
            "UPDATE media_item_export_regenerations SET state = 'failed', due_at = NULL, "
            "failure_reason = 'regeneration_failed' "
            "WHERE state IN ('queued', 'running', 'withdrawing') AND export_id IN "
            f"(SELECT id FROM media_item_exports WHERE media_item_id IN ({_ARCHIVED_ITEMS}))"
        )
    )


def _archive_access_grants(bind):
    op.create_table(
        "archived_media_access_rows",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_table", sa.String(), nullable=False),
        sa.Column("row_json", sa.Text(), nullable=False),
    )
    for table in _ACCESS_TABLES:
        rows = bind.execute(
            sa.text(f"SELECT * FROM {table} WHERE source_kind IN ('radarr', 'sonarr')")
        ).mappings()
        for row in rows.all():
            bind.execute(
                sa.text(
                    "INSERT INTO archived_media_access_rows (source_table, row_json) "
                    "VALUES (:table, :row)"
                ),
                {"table": table, "row": json.dumps(dict(row), default=str, sort_keys=True)},
            )
        bind.execute(sa.text(f"DELETE FROM {table} WHERE source_kind IN ('radarr', 'sonarr')"))
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_constraint("mediasourcekind", type_="check")
            batch.create_check_constraint(
                "mediasourcekind", "source_kind IN ('jellyfin_library', 'manual_files')"
            )


def upgrade():
    bind = op.get_bind()
    if bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Rebuilding media_items requires foreign keys off")
    _archive_media_items(bind)
    _stop_archived_work(bind)
    _archive_access_grants(bind)
    with op.batch_alter_table("arr_settings", recreate="always") as batch:
        batch.drop_column("api_key")


def downgrade():
    raise RuntimeError("Local migrations cannot restore removed Radarr and Sonarr support")
