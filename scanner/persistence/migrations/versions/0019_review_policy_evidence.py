"""Retain immutable review source bindings independently of editable membership."""

import sqlalchemy as sa
from alembic import op

revision = "0019_review_policy_evidence"
down_revision = "0018_jellyfin_account_sync"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_policy_evidence",
        sa.Column(
            "review_id",
            sa.String(),
            sa.ForeignKey("saved_reviews.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_finding_id", sa.Integer(), sa.ForeignKey("scan_findings.id"), primary_key=True
        ),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=True),
        sa.Column("sync_start", sa.Float(), nullable=False),
        sa.Column("sync_end", sa.Float(), nullable=False),
        sa.Column("retime", sa.Boolean(), nullable=False),
        sa.CheckConstraint("start_seconds >= 0", name="ck_review_policy_evidence_start"),
        sa.CheckConstraint(
            "end_seconds IS NULL OR end_seconds > start_seconds",
            name="ck_review_policy_evidence_end",
        ),
        sa.CheckConstraint(
            "sync_start >= 0 AND sync_end > sync_start", name="ck_review_policy_evidence_sync"
        ),
        sa.CheckConstraint("retime IN (0, 1)", name="ck_review_policy_evidence_retime"),
    )
    op.execute(
        "WITH bindings AS ("
        "SELECT m.review_id, f.id, f.start_seconds, f.end_seconds, "
        "y.start_seconds AS sync_start, y.end_seconds AS sync_end, "
        "(s.origin = 'imported' OR s.video_hash IS NULL) AS retime, "
        "COALESCE(d.material_start_seconds, 0) AS source_start, "
        "COALESCE(d.material_end_seconds, s.duration_seconds) AS source_end "
        "FROM review_scan_memberships m JOIN scans s ON s.id = m.scan_id "
        "JOIN scan_findings f ON f.scan_id = s.id "
        "JOIN review_synchronizations y ON y.review_id = m.review_id "
        "LEFT JOIN scan_mcf_documents d ON d.scan_id = s.id "
        "WHERE m.role = 'both' OR (m.role = 'audio' AND f.source = 'audio') "
        "OR (m.role = 'video' AND f.source = 'visual') "
        "OR (f.mcf_channel = 'both' AND EXISTS (SELECT 1 FROM community_imports c "
        "WHERE c.scan_id = s.id AND c.artifact_bytes IS NOT NULL))) "
        "INSERT INTO review_policy_evidence "
        "SELECT review_id, id, start_seconds, end_seconds, "
        "CASE WHEN retime AND source_end > source_start THEN source_start ELSE sync_start END, "
        "CASE WHEN retime AND source_end > source_start THEN source_end ELSE sync_end END, "
        "retime FROM bindings"
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard review policy evidence")
