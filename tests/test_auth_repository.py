import hashlib
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import scanner.security.auth_repository as auth_repository
from scanner.db import AuthSessionRow, Base
from scanner.models import canonicalize_username
from scanner.security.auth_repository import (
    AdministratorNotFoundError,
    authenticate_session,
    cleanup_expired_sessions,
    issue_session,
    refresh_session_activity,
    revoke_session_by_id,
    revoke_user_sessions,
    validate_csrf_token,
)
from scanner.settings.config import Settings
from tests.multi_user_access_support import add_linked_owner


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


@pytest.mark.parametrize(
    ("value", "display", "lookup"),
    [
        (f" {chr(0x2002)}Ａdmin{chr(0x2002)} ", "Admin", "admin"),
        ("Straße", "Straße", "strasse"),
        ("A  B", "A  B", "a  b"),
        ("a", "a", "a"),
        ("a" * 64, "a" * 64, "a" * 64),
    ],
)
def test_username_normalization_and_boundaries(value, display, lookup):
    assert canonicalize_username(value) == (display, lookup)


def test_username_nfkc_and_casefold_equivalents_share_a_lookup_value():
    assert canonicalize_username("Ａdmin")[1] == canonicalize_username("admin")[1]
    assert canonicalize_username("Straße")[1] == canonicalize_username("STRASSE")[1]


@pytest.mark.parametrize("value", ["   ", "a" * 65])
def test_username_rejects_invalid_lengths(value):
    with pytest.raises(ValueError):
        canonicalize_username(value)


@pytest.mark.parametrize(
    "character",
    [chr(0), chr(0x200B), chr(0xD800), chr(0xE000), chr(0x378), chr(0x2028), chr(0x2029)],
)
def test_username_rejects_all_disallowed_categories(character):
    with pytest.raises(ValueError):
        canonicalize_username(f"Admin{character}")


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _create_administrator(db_session):
    add_linked_owner(db_session, "Admin")


def test_issue_session_uses_independent_256_bit_csprng_values_and_digest_only_storage(
    db_session, monkeypatch, caplog
):
    _create_administrator(db_session)
    random_values = iter((bytes(range(32)), bytes(range(32, 64))))

    def token_bytes(size):
        assert size == 32
        return next(random_values)

    monkeypatch.setattr(auth_repository.secrets, "token_bytes", token_bytes)

    issued = issue_session(db_session, remember_me=False, now=_NOW)
    row = db_session.get(AuthSessionRow, issued.id)

    assert _TOKEN_PATTERN.fullmatch(issued.token)
    assert _TOKEN_PATTERN.fullmatch(issued.csrf_token)
    assert issued.token != issued.csrf_token
    assert row is not None
    assert row.token_digest == hashlib.sha256(
        b"editarr-session-v1\0" + bytes(range(32))
    ).hexdigest()
    assert row.csrf_token_digest == hashlib.sha256(
        b"editarr-csrf-v1\0" + bytes(range(32, 64))
    ).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", row.token_digest)
    assert re.fullmatch(r"[0-9a-f]{64}", row.csrf_token_digest)
    assert issued.token not in row.__dict__.values()
    assert issued.csrf_token not in row.__dict__.values()
    assert issued.token not in repr(issued)
    assert issued.csrf_token not in repr(issued)
    assert issued.token not in caplog.text
    assert issued.csrf_token not in caplog.text


@pytest.mark.parametrize(
    "token",
    ["", "a" * 42, "a" * 44, "a" * 43 + "=", "+" * 43, "=" * 43],
)
def test_session_lookup_rejects_noncanonical_or_malformed_tokens_before_authentication(
    db_session, token
):
    _create_administrator(db_session)
    issued = issue_session(db_session, remember_me=False, now=_NOW)

    assert authenticate_session(db_session, token, now=_NOW) is None
    assert authenticate_session(db_session, issued.token + "=", now=_NOW) is None


def test_session_authentication_and_csrf_validation_use_the_expected_digests(
    db_session, monkeypatch
):
    _create_administrator(db_session)
    issued = issue_session(db_session, remember_me=False, now=_NOW)
    authenticated = authenticate_session(db_session, issued.token, now=_NOW)
    original_compare_digest = auth_repository.hmac.compare_digest
    compared_values = []

    def compare_digest(first, second):
        compared_values.append((first, second))
        return original_compare_digest(first, second)

    monkeypatch.setattr(auth_repository.hmac, "compare_digest", compare_digest)

    assert authenticated is not None
    assert validate_csrf_token(authenticated, issued.csrf_token)
    assert not validate_csrf_token(authenticated, issued.token)
    assert not validate_csrf_token(authenticated, issued.csrf_token + "=")
    assert len(compared_values) == 2
    assert compared_values[0][0] == db_session.get(AuthSessionRow, issued.id).csrf_token_digest


def test_session_issuance_requires_an_existing_owner_and_rolls_back(db_session):
    with pytest.raises(AdministratorNotFoundError):
        issue_session(db_session, remember_me=False, now=_NOW)

    assert db_session.scalars(select(AuthSessionRow)).all() == []


def test_session_idle_and_absolute_expiry_never_authenticate_and_remove_presented_expiry(
    db_session,
):
    _create_administrator(db_session)
    idle_expired = issue_session(db_session, remember_me=False, now=_NOW)
    absolute_expired = issue_session(
        db_session,
        remember_me=False,
        now=_NOW,
        configuration=Settings(
            auth_session_idle_seconds=8 * 60 * 60,
            auth_session_absolute_seconds=8 * 60 * 60,
        ),
    )

    assert (
        authenticate_session(db_session, idle_expired.token, now=_NOW + timedelta(minutes=30))
        is None
    )
    assert db_session.get(AuthSessionRow, idle_expired.id) is None
    assert authenticate_session(
        db_session, absolute_expired.token, now=_NOW + timedelta(hours=8)
    ) is None
    assert db_session.get(AuthSessionRow, absolute_expired.id) is None


def test_remember_me_uses_the_independent_remembered_lifetimes(db_session):
    _create_administrator(db_session)

    issued = issue_session(db_session, remember_me=True, now=_NOW)

    assert issued.remember_me is True
    assert issued.idle_expires_at == _NOW + timedelta(days=7)
    assert issued.absolute_expires_at == _NOW + timedelta(days=30)
    assert authenticate_session(db_session, issued.token, now=_NOW + timedelta(days=7)) is None


def test_session_refresh_is_coalesced_advances_idle_only_and_never_moves_backwards(db_session):
    _create_administrator(db_session)
    issued = issue_session(
        db_session,
        remember_me=False,
        now=_NOW,
        configuration=Settings(
            auth_session_idle_seconds=8 * 60 * 60,
            auth_session_absolute_seconds=8 * 60 * 60,
        ),
    )
    authenticated = authenticate_session(db_session, issued.token, now=_NOW)

    assert authenticated is not None
    assert not refresh_session_activity(db_session, authenticated, now=_NOW + timedelta(minutes=4))
    assert refresh_session_activity(
        db_session, authenticated, now=_NOW + timedelta(hours=7, minutes=57)
    )
    row = db_session.get(AuthSessionRow, issued.id)
    assert row.last_used_at == (_NOW + timedelta(hours=7, minutes=57)).replace(tzinfo=None)
    assert row.idle_expires_at == (_NOW + timedelta(hours=8)).replace(tzinfo=None)
    assert row.absolute_expires_at == (_NOW + timedelta(hours=8)).replace(tzinfo=None)
    assert not refresh_session_activity(
        db_session, authenticated, now=_NOW + timedelta(hours=7, minutes=58)
    )


def test_session_refresh_uses_half_of_short_idle_lifetime_and_requires_explicit_eligibility(
    db_session,
):
    _create_administrator(db_session)
    configuration = Settings(
        auth_session_idle_seconds=8 * 60,
        auth_session_absolute_seconds=16 * 60,
    )
    issued = issue_session(db_session, remember_me=False, now=_NOW, configuration=configuration)
    authenticated = authenticate_session(db_session, issued.token, now=_NOW)

    assert authenticated is not None
    # Lookup and CSRF verification perform no session-row write; the caller decides eligibility.
    assert validate_csrf_token(authenticated, issued.csrf_token)
    assert db_session.get(AuthSessionRow, issued.id).last_used_at == _NOW.replace(tzinfo=None)
    assert not refresh_session_activity(
        db_session,
        authenticated,
        now=_NOW + timedelta(minutes=3, seconds=59),
        configuration=configuration,
    )
    assert refresh_session_activity(
        db_session, authenticated, now=_NOW + timedelta(minutes=4), configuration=configuration
    )


def test_current_and_mass_revocation_prevent_further_authentication(db_session):
    _create_administrator(db_session)
    first = issue_session(db_session, remember_me=False, now=_NOW)
    second = issue_session(db_session, remember_me=False, now=_NOW)

    assert revoke_session_by_id(db_session, first.id, now=_NOW + timedelta(seconds=1))
    assert not revoke_session_by_id(db_session, first.id, now=_NOW + timedelta(seconds=2))
    assert authenticate_session(db_session, first.token, now=_NOW + timedelta(seconds=2)) is None
    assert (
        authenticate_session(db_session, second.token, now=_NOW + timedelta(seconds=2))
        is not None
    )
    assert revoke_user_sessions(db_session, 1, now=_NOW + timedelta(seconds=3)) == 1
    assert authenticate_session(db_session, second.token, now=_NOW + timedelta(seconds=3)) is None


def test_session_refresh_cannot_restore_a_concurrently_revoked_session(db_session):
    _create_administrator(db_session)
    issued = issue_session(db_session, remember_me=False, now=_NOW)
    authenticated = authenticate_session(db_session, issued.token, now=_NOW)

    assert authenticated is not None
    assert revoke_session_by_id(db_session, issued.id, now=_NOW + timedelta(minutes=5))
    assert not refresh_session_activity(
        db_session, authenticated, now=_NOW + timedelta(minutes=5)
    )
    assert db_session.get(AuthSessionRow, issued.id).revoked_at == (
        _NOW + timedelta(minutes=5)
    ).replace(tzinfo=None)


def test_expired_session_cleanup_is_bounded_deterministic_and_reports_count(db_session):
    _create_administrator(db_session)
    active = issue_session(db_session, remember_me=False, now=_NOW)
    for index in range(501):
        is_revoked = index == 0
        db_session.add(
            AuthSessionRow(
                user_id=1,
                    token_digest=f"expired-{index}",
                    csrf_token_digest=f"csrf-{index}",
                    csrf_token_ciphertext="expired-session-test-envelope",
                created_at=_NOW.replace(tzinfo=None),
                last_used_at=_NOW.replace(tzinfo=None),
                absolute_expires_at=(
                    _NOW + timedelta(days=1) if is_revoked else _NOW - timedelta(seconds=1)
                ).replace(tzinfo=None),
                idle_expires_at=(
                    _NOW + timedelta(days=1) if is_revoked else _NOW - timedelta(seconds=1)
                ).replace(tzinfo=None),
                remember_me=False,
                revoked_at=_NOW.replace(tzinfo=None) if is_revoked else None,
            )
        )
    db_session.commit()

    assert cleanup_expired_sessions(db_session, now=_NOW) == 500
    remaining_ids = list(db_session.scalars(select(AuthSessionRow.id).order_by(AuthSessionRow.id)))

    assert remaining_ids == [active.id, active.id + 501]
    assert cleanup_expired_sessions(db_session, now=_NOW) == 1
    assert list(db_session.scalars(select(AuthSessionRow.id).order_by(AuthSessionRow.id))) == [
        active.id
    ]
