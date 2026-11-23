"""Retain sampled media evidence alongside legacy and full-file version identity."""

import sqlalchemy as sa
from alembic import op

revision = "0036_sampled_media_identity"
down_revision = "0035_stable_media_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "media_versions",
        sa.Column(
            "sampled_md5",
            sa.String(),
            sa.CheckConstraint(
                "sampled_md5 IS NULL OR (length(sampled_md5) = 32 "
                "AND sampled_md5 NOT GLOB '*[^0-9a-f]*')",
                name="ck_media_versions_sampled_md5",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "media_versions",
        sa.Column(
            "sample_size_bytes",
            sa.Integer(),
            sa.CheckConstraint(
                "(sample_size_bytes IS NULL AND sampled_md5 IS NULL) OR "
                "(sample_size_bytes IS NOT NULL AND sample_size_bytes >= 0 "
                "AND sampled_md5 IS NOT NULL)",
                name="ck_media_versions_sample_size",
            ),
            nullable=True,
        ),
    )
    op.add_column("media_version_locators", sa.Column("sample_generation", sa.String()))
    op.create_index(
        "ix_media_versions_title_sample",
        "media_versions",
        ["title_id", "sampled_md5", "sample_size_bytes"],
    )
    op.create_index(
        "ix_media_version_locators_sample_generation",
        "media_version_locators",
        ["resolved_path", "sample_generation"],
    )


def downgrade():
    saved = op.get_bind().exec_driver_sql(
        "SELECT 1 FROM media_versions WHERE sampled_md5 IS NOT NULL LIMIT 1"
    ).first()
    if saved:
        raise RuntimeError(
            "Restore a backup to downgrade without discarding sampled media evidence"
        )
    op.drop_index("ix_media_version_locators_sample_generation", "media_version_locators")
    op.drop_index("ix_media_versions_title_sample", "media_versions")
    op.drop_column("media_version_locators", "sample_generation")
    op.drop_column("media_versions", "sample_size_bytes")
    op.drop_column("media_versions", "sampled_md5")
