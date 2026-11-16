"""Reverse migrations 0027 to 0031 so a head database can stand in for an older revision."""

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.community_schema_fixture import SYNTHETIC_PASSWORD_HASH


def undo_explicit_filter_modes_migration(connection) -> None:
    """Let `user_filter_preferences.mode` be null again; every stored mode stays valid."""
    operations = Operations(MigrationContext.configure(connection))
    with operations.batch_alter_table("user_filter_preferences", recreate="always") as batch:
        batch.alter_column("mode", existing_type=sa.String(4), nullable=True)


def undo_remove_passwords_migration(connection) -> None:
    """Give the owner a local password again and restore what 0030 dropped, in place.

    An account 0030 disabled for having no link becomes active again.
    """
    operations = Operations(MigrationContext.configure(connection))
    with operations.batch_alter_table(
        "users", recreate="always", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.add_column(
            sa.Column("password_hash", sa.Text(), nullable=True),
            insert_after="normalized_username",
        )
        batch.add_column(
            sa.Column("local_credential", sa.String(), nullable=False, server_default="none"),
            insert_after="password_hash",
        )
        batch.add_column(
            sa.Column("password_changed_at", sa.DateTime(), nullable=True),
            insert_after="updated_at",
        )
        batch.drop_constraint("ck_users_disable_cause", type_="check")
        batch.drop_constraint("ck_users_owner_state", type_="check")
    connection.execute(
        sa.text(
            "UPDATE users SET status = 'active', disable_cause = NULL "
            "WHERE disable_cause = 'unlinked'"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE users SET password_hash = :hash, local_credential = 'set', "
            "password_changed_at = updated_at WHERE role = 'owner'"
        ),
        {"hash": SYNTHETIC_PASSWORD_HASH},
    )
    with operations.batch_alter_table(
        "users", recreate="always", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.alter_column("local_credential", server_default=None)
        batch.create_check_constraint(
            "ck_users_local_credential", "local_credential IN ('none', 'set', 'change_required')"
        )
        batch.create_check_constraint(
            "ck_users_password_state", "(password_hash IS NULL) = (local_credential = 'none')"
        )
        batch.create_check_constraint(
            "ck_users_local_login",
            "password_hash IS NULL OR "
            "(normalized_username IS NOT NULL AND password_changed_at IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_users_disable_cause",
            "disable_cause IS NULL OR (status = 'disabled' AND disable_cause IN "
            "('local', 'upstream_disabled', 'upstream_deleted'))",
        )
        batch.create_check_constraint(
            "ck_users_owner_state", "role != 'owner' OR status = 'active'"
        )
    with operations.batch_alter_table("account_settings", recreate="always") as batch:
        batch.add_column(
            sa.Column("registration_mode", sa.String(), nullable=False, server_default="managed"),
            insert_after="id",
        )
        batch.create_check_constraint(
            "ck_account_settings_registration_mode", "registration_mode IN ('managed', 'open')"
        )
    with operations.batch_alter_table("account_settings", recreate="always") as batch:
        batch.alter_column("registration_mode", server_default=None)


def undo_jellyfin_owner_migration(connection) -> None:
    """Restore 0010's owner check, which required a local password, with foreign keys off."""
    operations = Operations(MigrationContext.configure(connection))
    with operations.batch_alter_table(
        "users", recreate="always", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.drop_constraint("ck_users_owner_state", type_="check")
        batch.create_check_constraint(
            "ck_users_owner_state",
            "role != 'owner' OR (status = 'active' AND password_hash IS NOT NULL)",
        )


def undo_jellyfin_admission_migration(connection) -> None:
    """Restore the admission mode and explicit-role columns that 0028 dropped, in place."""
    operations = Operations(MigrationContext.configure(connection))
    with operations.batch_alter_table("account_settings", recreate="always") as batch:
        batch.add_column(
            sa.Column(
                "jellyfin_admission",
                sa.String(),
                # Inline, as 0018 added it, so 0028's DROP COLUMN takes the check with it.
                sa.CheckConstraint(
                    "jellyfin_admission IN ('first_login', 'import_only')",
                    name="ck_account_settings_jellyfin_admission",
                ),
                nullable=False,
                server_default="first_login",
            ),
            insert_after="updated_at",
        )
    with operations.batch_alter_table("user_provider_identities", recreate="always") as batch:
        batch.add_column(
            sa.Column("role_explicit", sa.Boolean(), nullable=False, server_default="0"),
            insert_after="link_state",
        )
    with operations.batch_alter_table("user_provider_identities", recreate="always") as batch:
        batch.alter_column("role_explicit", server_default=None)
