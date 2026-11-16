"""Account, permission, session, and API-key rows.

The checks mirror revision 0010_user_accounts exactly; the fresh-migration test
compares this metadata with the migrated schema.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import (
    relationship,
)

from .db_core import (
    Base,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserRow(Base):
    """One account. IDs are never reused, and at most one row holds the owner role."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'admin', 'user')", name="ck_users_role"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        CheckConstraint(
            "disable_cause IS NULL OR (status = 'disabled' AND disable_cause IN "
            "('local', 'upstream_disabled', 'upstream_deleted', 'unlinked'))",
            name="ck_users_disable_cause",
        ),
        CheckConstraint(
            "(username IS NULL) = (normalized_username IS NULL)", name="ck_users_username_pair"
        ),
        CheckConstraint(
            "role != 'owner' OR status = 'active' OR disable_cause IS 'unlinked'",
            name="ck_users_owner_state",
        ),
        CheckConstraint(
            "length(local_export_id) = 38 AND substr(local_export_id, 1, 6) = 'local-' "
            "AND substr(local_export_id, 7) NOT GLOB '*[^0-9a-f]*'",
            name="ck_users_local_export_id",
        ),
        CheckConstraint("permission_revision >= 1", name="ck_users_permission_revision"),
        UniqueConstraint("normalized_username", name="uq_users_normalized_username"),
        UniqueConstraint("local_export_id", name="uq_users_local_export_id"),
        Index("uq_users_single_owner", "role", unique=True, sqlite_where=text("role = 'owner'")),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True)
    role = Column(String, nullable=False)
    status = Column(String, nullable=False, default="active")
    username = Column(String, nullable=True)
    normalized_username = Column(String, nullable=True)
    local_export_id = Column(String, nullable=False)
    permission_revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, nullable=False)
    disable_cause = Column(String, nullable=True)

    sessions = relationship("AuthSessionRow", back_populates="user")


class UserProviderIdentityRow(Base):
    """A user's one Jellyfin identity, unique by server and canonical user ID."""

    __tablename__ = "user_provider_identities"
    __table_args__ = (
        CheckConstraint(
            "link_state IN ('verified', 'outage_grace', 'outage_expired', 'server_replaced')",
            name="ck_user_provider_identities_link_state",
        ),
        CheckConstraint("length(server_id) > 0", name="ck_user_provider_identities_server_id"),
        CheckConstraint(
            "length(provider_user_id) = 32 AND provider_user_id NOT GLOB '*[^0-9a-f]*' "
            "AND provider_user_id != '00000000000000000000000000000000'",
            name="ck_user_provider_identities_user_id",
        ),
        UniqueConstraint(
            "server_id", "provider_user_id", name="uq_user_provider_identities_provider"
        ),
        CheckConstraint(
            "observed_generation IS NULL OR observed_generation > 0",
            name="ck_user_provider_identities_observed_generation",
        ),
    )

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    server_id = Column(String, nullable=False)
    provider_user_id = Column(String, nullable=False)
    link_state = Column(String, nullable=False)
    last_verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, nullable=False)
    observed_generation = Column(Integer, nullable=True)
    """The read generation of the newest Jellyfin observation applied to this account (0018)."""


class UserProfileRow(Base):
    """Optional profile values and the last sign-in; no row means none of them (0017)."""

    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint(
            "display_name IS NULL OR length(display_name) BETWEEN 1 AND 100",
            name="ck_user_profiles_display_name",
        ),
        CheckConstraint(
            "email IS NULL OR length(email) BETWEEN 3 AND 254", name="ck_user_profiles_email"
        ),
        CheckConstraint(
            "timezone IS NULL OR length(timezone) BETWEEN 1 AND 64",
            name="ck_user_profiles_timezone",
        ),
        CheckConstraint(
            "language IS NULL OR length(language) BETWEEN 2 AND 35",
            name="ck_user_profiles_language",
        ),
    )

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    display_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    timezone = Column(String, nullable=True)
    language = Column(String, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=_now, nullable=False)


class UserAvatarRow(Base):
    """A local account's uploaded avatar, re-encoded as PNG and kept in the database."""

    __tablename__ = "user_avatars"
    __table_args__ = (
        CheckConstraint(
            "length(digest) = 64 AND digest NOT GLOB '*[^0-9a-f]*'",
            name="ck_user_avatars_digest",
        ),
        CheckConstraint("length(image) BETWEEN 1 AND 524288", name="ck_user_avatars_image"),
    )

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    digest = Column(String, nullable=False)
    image = Column(LargeBinary, nullable=False)
    updated_at = Column(DateTime, default=_now, nullable=False)


class AccountSettingsRow(Base):
    """Instance account settings, read by Jellyfin account sync; no row means the defaults."""

    __tablename__ = "account_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_account_settings_singleton"),
        CheckConstraint(
            "jellyfin_read_generation >= 0", name="ck_account_settings_jellyfin_read_generation"
        ),
        CheckConstraint(
            "jellyfin_clock_fence >= 0 AND jellyfin_clock_fence <= jellyfin_read_generation",
            name="ck_account_settings_jellyfin_clock_fence",
        ),
        CheckConstraint(
            "jellyfin_read_epoch IS NULL OR (length(jellyfin_read_epoch) = 32 "
            "AND jellyfin_read_epoch NOT GLOB '*[^0-9a-f]*')",
            name="ck_account_settings_jellyfin_read_epoch",
        ),
    )

    id = Column(Integer, primary_key=True, default=1)
    updated_at = Column(DateTime, default=_now, nullable=False)
    jellyfin_read_generation = Column(Integer, nullable=False, default=0, server_default="0")
    """The generation the latest Jellyfin read took; each read takes the next (0018)."""
    jellyfin_read_at = Column(DateTime, nullable=True)
    """The wall-clock time the latest Jellyfin read took its generation (0018)."""
    jellyfin_clock_fence = Column(Integer, nullable=False, default=0, server_default="0")
    """The generation of the read that last found the clock stepped back; reads below
    it never verify (0018)."""
    jellyfin_read_epoch = Column(String, nullable=True)
    """The random value reads take with their generation; a restore clears it, so a read
    begun before the restore never applies after it (0018)."""


class JellyfinAccountSyncRow(Base):
    """A linked account's last verified Jellyfin state and its latest refresh (0018).

    `verified_at` moves only on a successful verification; a failed refresh
    records `last_attempt_at` and `last_failure` and leaves it alone.
    """

    __tablename__ = "jellyfin_account_sync"
    __table_args__ = (
        CheckConstraint(
            "upstream_state IN ('enabled', 'disabled', 'deleted')",
            name="ck_jellyfin_account_sync_upstream_state",
        ),
        CheckConstraint(
            "length(jellyfin_name) BETWEEN 1 AND 256", name="ck_jellyfin_account_sync_name"
        ),
        CheckConstraint(
            "avatar_tag IS NULL OR length(avatar_tag) BETWEEN 1 AND 256",
            name="ck_jellyfin_account_sync_avatar_tag",
        ),
        CheckConstraint(
            "is_administrator IN (0, 1)", name="ck_jellyfin_account_sync_administrator"
        ),
        CheckConstraint(
            "length(media_policy) BETWEEN 2 AND 1048576",
            name="ck_jellyfin_account_sync_media_policy",
        ),
        CheckConstraint(
            "last_outcome IN ('verified', 'disabled', 'deleted', 'unverified', 'server_replaced')",
            name="ck_jellyfin_account_sync_outcome",
        ),
        CheckConstraint(
            "(last_failure IS NOT NULL) = (last_outcome IN ('unverified', 'server_replaced'))",
            name="ck_jellyfin_account_sync_failure",
        ),
    )

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    upstream_state = Column(String, nullable=False)
    jellyfin_name = Column(String, nullable=False)
    avatar_tag = Column(String, nullable=True)
    is_administrator = Column(Boolean, nullable=False)
    media_policy = Column(Text, nullable=False)
    verified_at = Column(DateTime, nullable=False)
    last_attempt_at = Column(DateTime, nullable=False)
    last_outcome = Column(String, nullable=False)
    last_failure = Column(String, nullable=True)
    updated_at = Column(DateTime, default=_now, nullable=False)


class JellyfinUserBlockRow(Base):
    """A Jellyfin user kept out of Editarr when their account was deleted (0029).

    Keyed like `user_provider_identities` by server and canonical user ID, and tied
    to no account but the one that blocked it, so it outlives the deletion. `name`
    is Jellyfin's name for the user when the account was deleted.
    """

    __tablename__ = "jellyfin_user_blocks"
    __table_args__ = (
        CheckConstraint("length(server_id) > 0", name="ck_jellyfin_user_blocks_server_id"),
        CheckConstraint(
            "length(provider_user_id) = 32 AND provider_user_id NOT GLOB '*[^0-9a-f]*' "
            "AND provider_user_id != '00000000000000000000000000000000'",
            name="ck_jellyfin_user_blocks_user_id",
        ),
        CheckConstraint(
            "name IS NULL OR length(name) BETWEEN 1 AND 256", name="ck_jellyfin_user_blocks_name"
        ),
    )

    server_id = Column(String, primary_key=True)
    provider_user_id = Column(String, primary_key=True)
    name = Column(String, nullable=True)
    blocked_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    blocked_at = Column(DateTime, nullable=False)


class RoleCapabilityDefaultRow(Base):
    """A changed admin or user default; a missing row means the catalog default."""

    __tablename__ = "role_capability_defaults"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_role_capability_defaults_role"),
    )

    role = Column(String, primary_key=True)
    capability = Column(String, primary_key=True)
    granted = Column(Boolean, nullable=False)
    updated_at = Column(DateTime, default=_now, nullable=False)


class UserCapabilityOverrideRow(Base):
    """An explicit allow or deny; inherit is the absence of a row."""

    __tablename__ = "user_capability_overrides"
    __table_args__ = (
        CheckConstraint("effect IN ('allow', 'deny')", name="ck_user_capability_overrides_effect"),
    )

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    capability = Column(String, primary_key=True)
    effect = Column(String, nullable=False)
    updated_at = Column(DateTime, default=_now, nullable=False)


class AuthSessionRow(Base):
    """A server-side session retaining token digests and a protected CSRF recovery copy."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_absolute_expires_at", "absolute_expires_at"),
        Index("ix_auth_sessions_idle_expires_at", "idle_expires_at"),
        Index("ix_auth_sessions_user_id", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_digest = Column(Text, nullable=False, unique=True)
    csrf_token_digest = Column(Text, nullable=False)
    csrf_token_ciphertext = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_used_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    absolute_expires_at = Column(DateTime, nullable=False)
    idle_expires_at = Column(DateTime, nullable=True)
    remember_me = Column(Boolean, nullable=False, default=False)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("UserRow", back_populates="sessions")


class AuthSettingsRow(Base):
    """Persisted authentication modes; this row does not signal administrator setup."""

    __tablename__ = "auth_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_auth_settings_singleton_id"),
        CheckConstraint("method IN ('forms', 'external')", name="ck_auth_settings_method"),
        CheckConstraint(
            "authentication_required IN ('enabled', 'disabled_for_local_addresses')",
            name="ck_auth_settings_required_mode",
        ),
    )

    id = Column(Integer, primary_key=True, default=1)
    method = Column(String, nullable=False)
    authentication_required = Column(String, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class ApiKeyRow(Base):
    """A user's one integration key: a digest plus a wrapped copy its holder may reveal.

    A null holder is a key migrated from an installation without an administrator;
    owner setup assigns it. `token_value` is plaintext from before revision 0015
    and holds a value only until the application seals it into `secret_envelope`.
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_api_keys_user_id"),
        UniqueConstraint("token_digest", name="uq_api_keys_token_digest"),
        CheckConstraint(
            "token_value IS NULL OR secret_envelope IS NULL", name="ck_api_keys_one_secret"
        ),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    token_digest = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)
    token_value = Column(Text, nullable=True)
    secret_envelope = Column(Text, nullable=True)
