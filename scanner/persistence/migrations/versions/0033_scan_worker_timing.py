"""Persist accumulated scan work and the current worker attempt separately."""

import sqlalchemy as sa
from alembic import op

revision = "0033_scan_worker_timing"
down_revision = "0032_manual_filter_tracking"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scans", sa.Column("worker_elapsed_seconds", sa.Float(), nullable=True))
    op.add_column("scans", sa.Column("worker_attempt_started_at", sa.DateTime(), nullable=True))
    # Old rows have one start for the whole scan. Use the last known worker activity
    # for interrupted scans and the recorded terminal update for finished scans.
    op.execute(
        """
        UPDATE scans
        SET worker_attempt_started_at = CASE
                WHEN status IN ('scanning', 'cancelling') THEN worker_started_at
            END,
            worker_elapsed_seconds = CASE
                WHEN status IN ('scanning', 'cancelling') THEN NULL
                WHEN status IN ('complete', 'error', 'failed', 'cancelled') THEN
                    max(0, round((julianday(updated_at) - julianday(worker_started_at)) * 86400, 3))
                WHEN worker_heartbeat_at IS NOT NULL THEN
                    max(0, round(
                        (julianday(worker_heartbeat_at) - julianday(worker_started_at)) * 86400, 3
                    ))
            END
        WHERE origin = 'generated' AND worker_started_at IS NOT NULL
        """
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard recorded worker timing")
