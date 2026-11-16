"""Store account profiles, uploaded avatars, and the local registration mode.

Adds three empty tables; no existing row changes. A missing profile row means no
profile values and no sign-in since this revision. A missing settings row means
managed registration, the default.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_user_profiles"
down_revision = "0016_personal_filter_rules"
branch_labels = None
depends_on = None


def _user_key():
    return sa.Column(
        "user_id",
        sa.Integer(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )


def upgrade():
    op.create_table(
        "user_profiles",
        _user_key(),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "display_name IS NULL OR length(display_name) BETWEEN 1 AND 100",
            name="ck_user_profiles_display_name",
        ),
        sa.CheckConstraint(
            "email IS NULL OR length(email) BETWEEN 3 AND 254", name="ck_user_profiles_email"
        ),
        sa.CheckConstraint(
            "timezone IS NULL OR length(timezone) BETWEEN 1 AND 64",
            name="ck_user_profiles_timezone",
        ),
        sa.CheckConstraint(
            "language IS NULL OR length(language) BETWEEN 2 AND 35",
            name="ck_user_profiles_language",
        ),
    )
    op.create_table(
        "user_avatars",
        _user_key(),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(digest) = 64 AND digest NOT GLOB '*[^0-9a-f]*'",
            name="ck_user_avatars_digest",
        ),
        sa.CheckConstraint("length(image) BETWEEN 1 AND 524288", name="ck_user_avatars_image"),
    )
    op.create_table(
        "account_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("registration_mode", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_account_settings_singleton"),
        sa.CheckConstraint(
            "registration_mode IN ('managed', 'open')",
            name="ck_account_settings_registration_mode",
        ),
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard account profiles")
