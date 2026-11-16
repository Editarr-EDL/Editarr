"""Store Jellyfin admission, why each account is disabled, and each linked account's Jellyfin state.

`account_settings` gains `jellyfin_admission`; an existing row reads as
`first_login`, the default, and no row still means the defaults. It also gains
the Jellyfin read counter: `jellyfin_read_generation` starts at 0 and
`jellyfin_read_at` null, since no read has been numbered yet, and
`jellyfin_clock_fence`, the generation of the read that last found the clock
stepped back, starts at 0: no read has found a step yet. `jellyfin_read_epoch`,
the random value every read takes with its generation and every restore
clears, starts null: the first read sets it. `users` gains
`disable_cause`; before this revision only the owner or an admin could disable
an account, so every disabled account becomes `local` and Jellyfin never
enables it. `user_provider_identities` gains `observed_generation`, the read
generation of the newest applied Jellyfin observation; it starts null, so the
first observation of each account applies. The new `jellyfin_account_sync` table
starts empty: no linked account has been verified by this revision yet, so none
has verified access to fall back on during an outage. No other existing value
changes.
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_jellyfin_account_sync"
down_revision = "0017_user_profiles"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE account_settings ADD COLUMN jellyfin_admission VARCHAR "
        "DEFAULT 'first_login' NOT NULL CONSTRAINT ck_account_settings_jellyfin_admission "
        "CHECK (jellyfin_admission IN ('first_login', 'import_only'))"
    )
    op.execute(
        "ALTER TABLE account_settings ADD COLUMN jellyfin_read_generation INTEGER "
        "DEFAULT '0' NOT NULL CONSTRAINT ck_account_settings_jellyfin_read_generation "
        "CHECK (jellyfin_read_generation >= 0)"
    )
    op.execute("ALTER TABLE account_settings ADD COLUMN jellyfin_read_at DATETIME")
    op.execute(
        "ALTER TABLE account_settings ADD COLUMN jellyfin_clock_fence INTEGER "
        "DEFAULT '0' NOT NULL CONSTRAINT ck_account_settings_jellyfin_clock_fence "
        "CHECK (jellyfin_clock_fence >= 0 AND jellyfin_clock_fence <= jellyfin_read_generation)"
    )
    op.execute(
        "ALTER TABLE account_settings ADD COLUMN jellyfin_read_epoch VARCHAR "
        "CONSTRAINT ck_account_settings_jellyfin_read_epoch CHECK (jellyfin_read_epoch IS NULL "
        "OR (length(jellyfin_read_epoch) = 32 AND jellyfin_read_epoch NOT GLOB '*[^0-9a-f]*'))"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN disable_cause VARCHAR "
        "CONSTRAINT ck_users_disable_cause CHECK (disable_cause IS NULL OR "
        "(status = 'disabled' AND disable_cause IN "
        "('local', 'upstream_disabled', 'upstream_deleted')))"
    )
    op.execute("UPDATE users SET disable_cause = 'local' WHERE status = 'disabled'")
    op.execute(
        "ALTER TABLE user_provider_identities ADD COLUMN observed_generation INTEGER "
        "CONSTRAINT ck_user_provider_identities_observed_generation "
        "CHECK (observed_generation IS NULL OR observed_generation > 0)"
    )
    op.create_table(
        "jellyfin_account_sync",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("upstream_state", sa.String(), nullable=False),
        sa.Column("jellyfin_name", sa.String(), nullable=False),
        sa.Column("avatar_tag", sa.String(), nullable=True),
        sa.Column("is_administrator", sa.Boolean(), nullable=False),
        sa.Column("media_policy", sa.Text(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("last_outcome", sa.String(), nullable=False),
        sa.Column("last_failure", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "upstream_state IN ('enabled', 'disabled', 'deleted')",
            name="ck_jellyfin_account_sync_upstream_state",
        ),
        sa.CheckConstraint(
            "length(jellyfin_name) BETWEEN 1 AND 256", name="ck_jellyfin_account_sync_name"
        ),
        sa.CheckConstraint(
            "avatar_tag IS NULL OR length(avatar_tag) BETWEEN 1 AND 256",
            name="ck_jellyfin_account_sync_avatar_tag",
        ),
        sa.CheckConstraint(
            "is_administrator IN (0, 1)", name="ck_jellyfin_account_sync_administrator"
        ),
        sa.CheckConstraint(
            "length(media_policy) BETWEEN 2 AND 1048576",
            name="ck_jellyfin_account_sync_media_policy",
        ),
        sa.CheckConstraint(
            "last_outcome IN ('verified', 'disabled', 'deleted', 'unverified', 'server_replaced')",
            name="ck_jellyfin_account_sync_outcome",
        ),
        sa.CheckConstraint(
            "(last_failure IS NOT NULL) = (last_outcome IN ('unverified', 'server_replaced'))",
            name="ck_jellyfin_account_sync_failure",
        ),
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard Jellyfin account state")
