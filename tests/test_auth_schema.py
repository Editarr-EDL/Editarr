import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner.db import (
    AuthSessionRow,
    AuthSettingsRow,
    Base,
    MediaItemRow,
    RoleCapabilityDefaultRow,
    UserCapabilityOverrideRow,
    UserProviderIdentityRow,
    UserRow,
)

_NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _account(name, *, role="owner", **values):
    fields = {
        "role": role,
        "status": "active",
        "username": name,
        "normalized_username": name,
        "local_export_id": "local-" + hashlib.md5(name.encode()).hexdigest(),
    }
    fields.update(values)
    return UserRow(**fields)


def _rejected(db_session, *rows):
    db_session.add_all(rows)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_authentication_schema_creates_tables_constraints_and_indexes(db_session):
    inspector = inspect(db_session.get_bind())

    assert {
        "users",
        "user_provider_identities",
        "role_capability_defaults",
        "user_capability_overrides",
        "auth_sessions",
        "auth_settings",
        "api_keys",
    } <= set(inspector.get_table_names())
    assert "admin_users" not in inspector.get_table_names()
    assert {index["name"] for index in inspector.get_indexes("users")} == {"uq_users_single_owner"}
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("auth_settings")
    } == {
        "ck_auth_settings_method",
        "ck_auth_settings_required_mode",
        "ck_auth_settings_singleton_id",
    }
    assert {index["name"] for index in inspector.get_indexes("auth_sessions")} == {
        "ix_auth_sessions_absolute_expires_at",
        "ix_auth_sessions_idle_expires_at",
        "ix_auth_sessions_user_id",
    }
    assert inspector.get_unique_constraints("auth_sessions") == [
        {"name": None, "column_names": ["token_digest"]}
    ]
    for table in ("auth_sessions", "api_keys"):
        assert inspector.get_foreign_keys(table) == [
            {
                "name": None,
                "constrained_columns": ["user_id"],
                "referred_schema": None,
                "referred_table": "users",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            }
        ]


def test_database_rejects_a_second_owner_but_accepts_other_roles(db_session):
    db_session.add(_account("first"))
    db_session.commit()

    _rejected(db_session, _account("second"))
    db_session.add_all([_account("admin", role="admin"), _account("user", role="user")])
    db_session.commit()
    assert db_session.scalars(select(UserRow.role).order_by(UserRow.id)).all() == [
        "owner",
        "admin",
        "user",
    ]


@pytest.mark.parametrize(
    "values",
    [
        {"status": "disabled"},
        {"status": "disabled", "disable_cause": "local"},
        {"status": "active", "disable_cause": "unlinked"},
        {"role": "superuser"},
        {"normalized_username": None},
        {"local_export_id": "local-ABCDEF0123456789abcdef0123456789"},
        {"local_export_id": "0123456789abcdef0123456789abcdef"},
        {"permission_revision": 0},
    ],
)
def test_owner_row_checks_refuse_invalid_account_states(db_session, values):
    _rejected(db_session, _account("owner", **values))


def test_an_owner_needs_no_username_and_may_be_disabled_only_for_having_no_link(db_session):
    db_session.add(_account("owner", username=None, normalized_username=None))
    db_session.commit()
    owner = db_session.scalar(select(UserRow))
    owner.status, owner.disable_cause = "disabled", "unlinked"
    db_session.commit()


def test_users_hold_no_password_columns(db_session):
    columns = {column["name"] for column in inspect(db_session.get_bind()).get_columns("users")}
    assert {"password_hash", "local_credential", "password_changed_at"}.isdisjoint(columns)


def test_local_export_ids_and_usernames_are_unique(db_session):
    db_session.add(_account("first"))
    db_session.commit()
    first = db_session.scalar(select(UserRow))
    _rejected(db_session, _account("second", role="user", local_export_id=first.local_export_id))
    _rejected(db_session, _account("first", role="user"))


def test_provider_identity_is_canonical_and_unique_per_server(db_session):
    db_session.add_all(
        [_account("owner"), _account("one", role="user"), _account("two", role="user")]
    )
    db_session.commit()
    jellyfin = "0123456789abcdef0123456789abcdef"

    def identity(user_id, server="server-a", provider_user_id=jellyfin, state="verified"):
        return UserProviderIdentityRow(
            user_id=user_id,
            server_id=server,
            provider_user_id=provider_user_id,
            link_state=state,
        )

    db_session.add(identity(2))
    db_session.commit()
    _rejected(db_session, identity(3))
    _rejected(db_session, identity(3, provider_user_id=jellyfin.upper()))
    _rejected(db_session, identity(3, provider_user_id="0" * 32))
    _rejected(db_session, identity(3, server=""))
    _rejected(db_session, identity(3, state="unlinked"))
    db_session.add(identity(3, server="server-b"))
    db_session.commit()


def test_permission_rows_store_only_explicit_choices(db_session):
    db_session.add(_account("owner"))
    db_session.commit()
    _rejected(db_session, UserCapabilityOverrideRow(user_id=1, capability="x", effect="inherit"))
    _rejected(db_session, RoleCapabilityDefaultRow(role="owner", capability="x", granted=True))
    db_session.add_all(
        [
            UserCapabilityOverrideRow(user_id=1, capability="x", effect="deny"),
            RoleCapabilityDefaultRow(role="admin", capability="x", granted=False),
        ]
    )
    db_session.commit()


def test_auth_settings_do_not_indicate_administrator_setup(db_session):
    db_session.add(AuthSettingsRow(method="external", authentication_required="enabled"))
    db_session.commit()

    assert db_session.scalar(select(UserRow.id)) is None


def test_unrelated_domain_rows_cannot_create_an_administrator(db_session):
    db_session.add(
        MediaItemRow(
            id="media-1",
            server_id="server-1",
            jellyfin_id="jellyfin-1",
            item_type="Movie",
            name="A movie",
        )
    )
    db_session.commit()
    db_session.delete(db_session.get(MediaItemRow, "media-1"))
    db_session.commit()

    assert db_session.scalar(select(UserRow.id)) is None


def test_auth_session_persists_only_explicit_digest_and_protected_recovery_columns(db_session):
    now = datetime.now(timezone.utc)
    db_session.add(_account("admin"))
    db_session.commit()
    db_session.add(
        AuthSessionRow(
            user_id=1,
            token_digest="token-digest",
            csrf_token_digest="csrf-digest",
            csrf_token_ciphertext="v1.test-session-envelope",
            last_used_at=now,
            absolute_expires_at=now + timedelta(hours=8),
        )
    )
    db_session.commit()

    column_names = {
        column["name"] for column in inspect(db_session.get_bind()).get_columns("auth_sessions")
    }
    assert {"token_digest", "csrf_token_digest", "csrf_token_ciphertext"} <= column_names
    assert "token" not in column_names
    assert "csrf_token" not in column_names
