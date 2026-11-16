"""Archive Other Files records now that every media item comes from Jellyfin.

Every row stays. A media item from Other Files gets `archived_source`
`manual_files`, and its scans, reviews, and exports become read-only through it.
Unfinished work on those items stops the way 0025 stops Radarr and Sonarr work.
A publication not yet sent ends cancelled; one already sent keeps its state so the
service is still polled, and carries a cancel request.
Other Files scope grants and every unmatched-media exception, which only Other
Files items could need, move to `archived_media_access_rows` as the JSON of the
original row. The exception table goes with them.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0026_archive_manual_files"
down_revision = "0025_archive_arr_sources"
branch_labels = None
depends_on = None

_ARCHIVED_ITEMS = "SELECT id FROM media_items WHERE archived_source = 'manual_files'"
_ARCHIVED_SCANS = f"SELECT id FROM scans WHERE media_item_id IN ({_ARCHIVED_ITEMS})"
_ARCHIVED_REVIEWS = f"SELECT id FROM saved_reviews WHERE media_item_id IN ({_ARCHIVED_ITEMS})"
_TERMINAL_SCANS = "('complete', 'error', 'failed', 'cancelled')"
_ARCHIVED_PUBLICATIONS = (
    "SELECT operation_id FROM community_operations WHERE kind = 'publish' "
    "AND state IN ({states}) AND (scan_id IN ({scans}) OR operation_id IN "
    "(SELECT operation_id FROM community_publications WHERE "
    "(source_kind = 'scan' AND source_id IN ({scans})) OR "
    "(source_kind = 'review' AND source_id IN ({reviews}))))"
)


def _publications(*states):
    quoted = ", ".join(f"'{state}'" for state in states)
    return _ARCHIVED_PUBLICATIONS.format(
        states=quoted, scans=_ARCHIVED_SCANS, reviews=_ARCHIVED_REVIEWS
    )


def _archive_media_items(bind):
    with op.batch_alter_table("media_items", recreate="always") as batch:
        batch.drop_constraint("archivedsource", type_="check")
        batch.alter_column(
            "archived_source", type_=sa.String(12), existing_type=sa.String(6), nullable=True
        )
        batch.create_check_constraint(
            "archivedsource", "archived_source IN ('radarr', 'sonarr', 'manual_files')"
        )
    bind.execute(
        sa.text(
            "UPDATE media_items SET archived_source = 'manual_files' "
            "WHERE server_id = 'editarr-manual'"
        )
    )


def _cancel_archived_publications(bind):
    bind.execute(
        sa.text(
            "UPDATE community_publications SET cancel_requested = 1 "
            f"WHERE operation_id IN ({_publications('pending', 'submitted')})"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE community_operations SET state = 'failed' "
            f"WHERE operation_id IN ({_publications('pending')})"
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


def _archive_rows(bind, table, where):
    rows = bind.execute(sa.text(f"SELECT * FROM {table} WHERE {where}")).mappings()
    for row in rows.all():
        bind.execute(
            sa.text(
                "INSERT INTO archived_media_access_rows (source_table, row_json) "
                "VALUES (:table, :row)"
            ),
            {"table": table, "row": json.dumps(dict(row), default=str, sort_keys=True)},
        )


def _archive_access_grants(bind):
    manual = "source_kind = 'manual_files'"
    _archive_rows(bind, "media_access_scope_sources", manual)
    bind.execute(sa.text(f"DELETE FROM media_access_scope_sources WHERE {manual}"))
    with op.batch_alter_table("media_access_scope_sources", recreate="always") as batch:
        batch.drop_constraint("mediasourcekind", type_="check")
        batch.create_check_constraint("mediasourcekind", "source_kind IN ('jellyfin_library')")
    _archive_rows(bind, "media_access_exceptions", "1 = 1")
    op.drop_table("media_access_exceptions")


def upgrade():
    bind = op.get_bind()
    if bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Rebuilding media_items requires foreign keys off")
    _archive_media_items(bind)
    _stop_archived_work(bind)
    _archive_access_grants(bind)


def downgrade():
    raise RuntimeError("Local migrations cannot restore removed Other Files support")
