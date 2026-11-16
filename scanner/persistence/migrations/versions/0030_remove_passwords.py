"""Remove local passwords; an account without a Jellyfin link is disabled.

`users` loses `password_hash`, `local_credential`, `password_changed_at`, and
their checks. `disable_cause` admits `unlinked`, and the owner may be disabled
only for that cause. Every active account without a Jellyfin link becomes
disabled with cause `unlinked` and a new permission revision; an account that
was already disabled keeps its cause. Every session of an account without a
link or with a local password is revoked, so each live session came from
Jellyfin. Every account without a link, the owner
included, gives up its local username so a Jellyfin user can take the name; the
name moves to the profile display name unless one is already set.
`account_settings` loses
`registration_mode`. Every other row stays, and so does the `users`
AUTOINCREMENT high-water mark, which rebuilding the table would otherwise reset.
Upgrading an existing database then runs VACUUM (`adoption`), so no page of the
live file keeps a deleted hash. The pre-upgrade safety snapshot and earlier
backups still hold them; the README says how to delete them.
"""

import re
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0030_remove_passwords"
down_revision = "0029_jellyfin_user_blocks"
branch_labels = None
depends_on = None

_REGISTRATION_COLUMN = re.compile(r"\s*registration_mode VARCHAR NOT NULL,")
_REGISTRATION_CHECK = re.compile(
    r",\s*CONSTRAINT ck_account_settings_registration_mode CHECK "
    r"\(registration_mode IN \('managed', 'open'\)\)"
)


_UNLINKED = "id NOT IN (SELECT user_id FROM user_provider_identities)"


def upgrade():
    bind = op.get_bind()
    if bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Rebuilding users requires foreign keys off")
    now = {"now": datetime.now(timezone.utc).replace(tzinfo=None)}
    # Linking re-enables an unlinked account without touching its sessions, and a
    # temporary-password session loses its restriction here, so neither may outlive 0030.
    bind.execute(
        sa.text(
            "UPDATE auth_sessions SET revoked_at = :now WHERE revoked_at IS NULL AND user_id IN "
            f"(SELECT id FROM users WHERE local_credential != 'none' OR {_UNLINKED})"
        ),
        now,
    )
    _rebuild_users(bind)
    bind.execute(
        sa.text(
            "UPDATE users SET status = 'disabled', disable_cause = 'unlinked', "
            "permission_revision = permission_revision + 1, updated_at = :now "
            f"WHERE status = 'active' AND {_UNLINKED}"
        ),
        now,
    )
    _release_unlinked_usernames(bind, now)
    _drop_registration_mode(bind)


def _release_unlinked_usernames(bind, now):
    """Move each unlinked account's username into an empty display name, then clear it."""

    named = f"username IS NOT NULL AND {_UNLINKED}"
    bind.execute(
        sa.text(
            "INSERT INTO user_profiles (user_id, display_name, updated_at) "
            f"SELECT id, username, :now FROM users WHERE {named} "
            "ON CONFLICT (user_id) DO UPDATE SET display_name = excluded.display_name, "
            "updated_at = excluded.updated_at WHERE user_profiles.display_name IS NULL"
        ),
        now,
    )
    bind.execute(
        sa.text(
            "UPDATE users SET username = NULL, normalized_username = NULL, updated_at = :now "
            f"WHERE {named}"
        ),
        now,
    )


def _rebuild_users(bind):
    high_water = bind.execute(
        sa.text("SELECT seq FROM sqlite_sequence WHERE name = 'users'")
    ).scalar()
    with op.batch_alter_table(
        "users", recreate="always", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        for name in (
            "ck_users_local_credential",
            "ck_users_password_state",
            "ck_users_local_login",
            "ck_users_disable_cause",
            "ck_users_owner_state",
        ):
            batch.drop_constraint(name, type_="check")
        for column in ("password_hash", "local_credential", "password_changed_at"):
            batch.drop_column(column)
        batch.create_check_constraint(
            "ck_users_disable_cause",
            "disable_cause IS NULL OR (status = 'disabled' AND disable_cause IN "
            "('local', 'upstream_disabled', 'upstream_deleted', 'unlinked'))",
        )
        batch.create_check_constraint(
            "ck_users_owner_state",
            "role != 'owner' OR status = 'active' OR disable_cause IS 'unlinked'",
        )
    if high_water is not None:
        bind.execute(sa.text("DELETE FROM sqlite_sequence WHERE name = 'users'"))
        bind.execute(
            sa.text("INSERT INTO sqlite_sequence (name, seq) VALUES ('users', :seq)"),
            {"seq": high_water},
        )


def _drop_registration_mode(bind):
    """Rebuild `account_settings` from its own stored definition minus the column.

    Native DROP COLUMN refuses a column named in a table check, and a batch
    rebuild would turn 0018's inline column checks into table checks.
    """
    created = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'account_settings'")
    ).scalar()
    without_column, columns = _REGISTRATION_COLUMN.subn("", created)
    replacement, checks = _REGISTRATION_CHECK.subn("", without_column)
    if (columns, checks) != (1, 1):
        raise RuntimeError("account_settings does not have the expected registration column")
    kept = [
        row[1]
        for row in bind.execute(sa.text("PRAGMA table_info(account_settings)"))
        if row[1] != "registration_mode"
    ]
    listed = ", ".join(kept)
    bind.execute(sa.text("ALTER TABLE account_settings RENAME TO _account_settings_old"))
    bind.execute(sa.text(replacement))
    bind.execute(
        sa.text(
            f"INSERT INTO account_settings ({listed}) SELECT {listed} FROM _account_settings_old"
        )
    )
    bind.execute(sa.text("DROP TABLE _account_settings_old"))


def downgrade():
    raise RuntimeError("Local migrations cannot restore deleted password hashes")
