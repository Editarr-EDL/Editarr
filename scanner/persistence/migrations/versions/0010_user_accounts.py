"""Turn the singleton administrator, sessions, and API key into per-user records.

The administrator keeps ID 1, username, and password hash as the sole owner. Its
API key keeps its digest and stored value. Every existing browser session is
revoked. A key without an administrator keeps a null holder until owner setup.
"""

import secrets
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0010_user_accounts"
down_revision = "0009_readable_api_key"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def _create_users():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("normalized_username", sa.String(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("local_credential", sa.String(), nullable=False),
        sa.Column("local_export_id", sa.String(), nullable=False),
        sa.Column("permission_revision", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.Column("password_changed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("role IN ('owner', 'admin', 'user')", name="ck_users_role"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        sa.CheckConstraint(
            "local_credential IN ('none', 'set', 'change_required')",
            name="ck_users_local_credential",
        ),
        sa.CheckConstraint(
            "(password_hash IS NULL) = (local_credential = 'none')",
            name="ck_users_password_state",
        ),
        sa.CheckConstraint(
            "(username IS NULL) = (normalized_username IS NULL)", name="ck_users_username_pair"
        ),
        sa.CheckConstraint(
            "password_hash IS NULL OR "
            "(normalized_username IS NOT NULL AND password_changed_at IS NOT NULL)",
            name="ck_users_local_login",
        ),
        sa.CheckConstraint(
            "role != 'owner' OR (status = 'active' AND password_hash IS NOT NULL)",
            name="ck_users_owner_state",
        ),
        sa.CheckConstraint(
            "length(local_export_id) = 38 AND substr(local_export_id, 1, 6) = 'local-' "
            "AND substr(local_export_id, 7) NOT GLOB '*[^0-9a-f]*'",
            name="ck_users_local_export_id",
        ),
        sa.CheckConstraint("permission_revision >= 1", name="ck_users_permission_revision"),
        sa.UniqueConstraint("normalized_username", name="uq_users_normalized_username"),
        sa.UniqueConstraint("local_export_id", name="uq_users_local_export_id"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "uq_users_single_owner",
        "users",
        ["role"],
        unique=True,
        sqlite_where=sa.text("role = 'owner'"),
    )


def _create_permission_tables():
    op.create_table(
        "user_provider_identities",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("server_id", sa.String(), nullable=False),
        sa.Column("provider_user_id", sa.String(), nullable=False),
        sa.Column("link_state", sa.String(), nullable=False),
        sa.Column("role_explicit", sa.Boolean(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "link_state IN ('verified', 'outage_grace', 'outage_expired', 'server_replaced')",
            name="ck_user_provider_identities_link_state",
        ),
        sa.CheckConstraint("length(server_id) > 0", name="ck_user_provider_identities_server_id"),
        sa.CheckConstraint(
            "length(provider_user_id) = 32 AND provider_user_id NOT GLOB '*[^0-9a-f]*' "
            "AND provider_user_id != '00000000000000000000000000000000'",
            name="ck_user_provider_identities_user_id",
        ),
        sa.UniqueConstraint(
            "server_id", "provider_user_id", name="uq_user_provider_identities_provider"
        ),
    )
    op.create_table(
        "role_capability_defaults",
        sa.Column("role", sa.String(), primary_key=True),
        sa.Column("capability", sa.String(), primary_key=True),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_role_capability_defaults_role"),
    )
    op.create_table(
        "user_capability_overrides",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("capability", sa.String(), primary_key=True),
        sa.Column("effect", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "effect IN ('allow', 'deny')", name="ck_user_capability_overrides_effect"
        ),
    )


def upgrade():
    bind = op.get_bind()
    # SQLAlchemy's SQLite DateTime storage format, so ORM reads parse it.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    op.execute("ALTER TABLE auth_sessions RENAME TO legacy_auth_sessions")
    op.execute("ALTER TABLE api_keys RENAME TO legacy_api_keys")

    _create_users()
    bind.execute(
        sa.text(
            "INSERT INTO users (id, role, status, username, normalized_username, "
            "password_hash, local_credential, local_export_id, permission_revision, "
            "created_at, updated_at, password_changed_at) "
            "SELECT id, 'owner', 'active', username, normalized_username, password_hash, "
            "'set', :export_id, 1, created_at, updated_at, password_changed_at "
            "FROM admin_users"
        ),
        {"export_id": f"local-{secrets.token_hex(16)}"},
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("token_digest", sa.Text(), nullable=False, unique=True),
        sa.Column("csrf_token_digest", sa.Text(), nullable=False),
        sa.Column("csrf_token_ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(), nullable=True),
        sa.Column("remember_me", sa.Boolean(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    bind.execute(
        sa.text(
            "INSERT INTO auth_sessions (id, user_id, token_digest, csrf_token_digest, "
            "csrf_token_ciphertext, created_at, last_used_at, absolute_expires_at, "
            "idle_expires_at, remember_me, revoked_at) "
            "SELECT id, admin_user_id, token_digest, csrf_token_digest, csrf_token_ciphertext, "
            "created_at, last_used_at, absolute_expires_at, idle_expires_at, remember_me, "
            "COALESCE(revoked_at, :now) FROM legacy_auth_sessions"
        ),
        {"now": now},
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("token_digest", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("token_value", sa.Text(), nullable=True),
        sa.UniqueConstraint("user_id", name="uq_api_keys_user_id"),
        sa.UniqueConstraint("token_digest", name="uq_api_keys_token_digest"),
    )
    bind.execute(
        sa.text(
            "INSERT INTO api_keys (id, user_id, token_digest, created_at, token_value) "
            "SELECT id, (SELECT id FROM users WHERE role = 'owner'), token_digest, "
            "created_at, token_value FROM legacy_api_keys"
        )
    )

    op.execute("DROP TABLE legacy_auth_sessions")
    op.execute("DROP TABLE legacy_api_keys")
    op.execute("DROP TABLE admin_users")
    op.create_index(
        "ix_auth_sessions_absolute_expires_at", "auth_sessions", ["absolute_expires_at"]
    )
    op.create_index("ix_auth_sessions_idle_expires_at", "auth_sessions", ["idle_expires_at"])
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    _create_permission_tables()


def downgrade():
    raise RuntimeError("Local migrations cannot discard user accounts or credentials")
