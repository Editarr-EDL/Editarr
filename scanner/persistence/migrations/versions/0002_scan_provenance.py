"""Record scan provenance and permit genuinely unknown finding confidence.

Revision ID: 0002_scan_provenance
Revises: 0001_current_baseline
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_scan_provenance"
down_revision = "0001_current_baseline"
branch_labels = None
depends_on = None


def upgrade():
    for name in (
        "release_version",
        "scanner_behavior_version",
        "build_revision",
        "audio_model_revision",
        "video_model_revision",
    ):
        op.add_column("scans", sa.Column(name, sa.String(), nullable=True))

    op.create_table(
        "scan_stage_coverage",
        sa.Column(
            "scan_id", sa.String(), sa.ForeignKey("scans.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("stage", sa.String(), primary_key=True),
        sa.Column("execution_id", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("release_version", sa.String(), nullable=True),
        sa.Column("scanner_behavior_version", sa.String(), nullable=True),
        sa.Column("build_revision", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("model_revision", sa.String(), nullable=True),
        sa.Column("model_digest", sa.String(), nullable=True),
        sa.Column("quantization", sa.String(), nullable=True),
        sa.Column("effective_settings_json", sa.Text(), nullable=True),
        sa.Column("intervals_json", sa.Text(), nullable=True),
        sa.Column("sampling_seconds", sa.Float(), nullable=True),
        sa.CheckConstraint("length(stage) > 0", name="ck_scan_stage_coverage_stage"),
        sa.CheckConstraint("length(execution_id) > 0", name="ck_scan_stage_coverage_execution"),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'failed', 'disabled', 'unsupported', 'unknown')",
            name="ck_scan_stage_coverage_status",
        ),
        sa.CheckConstraint(
            "source IN ('generated', 'manual', 'imported')",
            name="ck_scan_stage_coverage_source",
        ),
        sa.CheckConstraint(
            "sampling_seconds IS NULL OR (sampling_seconds > 0 AND sampling_seconds < 1e308)",
            name="ck_scan_stage_coverage_sampling",
        ),
        sa.CheckConstraint(
            "intervals_json IS NULL OR (length(intervals_json) <= 16384 "
            "AND json_valid(intervals_json))",
            name="ck_scan_stage_coverage_intervals",
        ),
        sa.CheckConstraint(
            "effective_settings_json IS NULL OR (length(effective_settings_json) <= 16384 "
            "AND json_valid(effective_settings_json))",
            name="ck_scan_stage_coverage_settings",
        ),
    )
    for table in ("scan_findings", "review_filters"):
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.alter_column("confidence", existing_type=sa.Float(), nullable=True)


def downgrade():
    raise RuntimeError("Local database migrations cannot discard scan provenance")
