"""Opaque authentication sessions and CSRF persistence."""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from scanner.db import AuthSessionRow, UserRow
from scanner.persistence.rows.db_auth import UserProfileRow
from scanner.security.access.identity import AccountStatus
from scanner.security.access.vocabulary import Role
from scanner.settings.config import Settings, settings

from .auth_crypto import (
    CsrfEncryptionKey,
    CsrfEnvelopeError,
    decrypt_csrf_token,
    encrypt_csrf_token,
    envelope_key_id,
    load_csrf_encryption_key,
)
from .auth_repository_admin import AdministratorNotFoundError, owner_id


class InvalidOpaqueTokenError(ValueError):
    """Raised when a session or CSRF token is not canonical base64url."""


_OPAQUE_TOKEN_BYTES = 32
_OPAQUE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_SESSION_DIGEST_PREFIX = b"editarr-session-v1\0"
_CSRF_DIGEST_PREFIX = b"editarr-csrf-v1\0"
_SESSION_TOUCH_INTERVAL = timedelta(minutes=5)
_SESSION_CLEANUP_BATCH_SIZE = 500


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """The only boundary that exposes newly-created bearer and CSRF values."""

    id: int
    token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    created_at: datetime
    last_used_at: datetime
    absolute_expires_at: datetime
    idle_expires_at: datetime | None
    remember_me: bool


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """Validated session state plus its account's current role and status."""

    id: int
    user_id: int
    role: Role
    status: AccountStatus
    created_at: datetime
    last_used_at: datetime
    absolute_expires_at: datetime
    idle_expires_at: datetime | None
    remember_me: bool
    _csrf_token_digest: str = field(repr=False)
    _token_digest: str = field(repr=False)
    _csrf_token_ciphertext: str = field(repr=False)


def issue_session(
    db: Session,
    *,
    remember_me: bool,
    user_id: int | None = None,
    now: datetime | None = None,
    configuration: Settings = settings,
    csrf_encryption_key: CsrfEncryptionKey | None = None,
    commit: bool = True,
) -> IssuedSession:
    """Create a session for ``user_id`` (default: the owner) and expose its raw values once."""

    user_id = owner_id(db) if user_id is None else user_id
    if user_id is None or db.get(UserRow, user_id) is None:
        raise AdministratorNotFoundError("An administrator is required before issuing a session")

    now = _utc_now(now)
    token = _new_opaque_token()
    csrf_token = _new_opaque_token()
    token_digest = _digest_opaque_token(token, prefix=_SESSION_DIGEST_PREFIX)
    csrf_encryption_key = csrf_encryption_key or load_csrf_encryption_key(configuration.data_dir)
    idle_lifetime, absolute_lifetime = _session_lifetimes(
        remember_me=remember_me, configuration=configuration
    )
    absolute_expires_at = now + absolute_lifetime
    row = AuthSessionRow(
        user_id=user_id,
        token_digest=token_digest,
        csrf_token_digest=_digest_opaque_token(csrf_token, prefix=_CSRF_DIGEST_PREFIX),
        csrf_token_ciphertext=encrypt_csrf_token(
            csrf_token, token_digest, csrf_encryption_key
        ),
        created_at=_database_timestamp(now),
        last_used_at=_database_timestamp(now),
        absolute_expires_at=_database_timestamp(absolute_expires_at),
        idle_expires_at=_database_timestamp(now + idle_lifetime),
        remember_me=remember_me,
    )
    db.add(row)
    db.execute(
        sqlite_insert(UserProfileRow)
        .values(user_id=user_id, last_login_at=row.created_at, updated_at=row.created_at)
        .on_conflict_do_update(
            index_elements=[UserProfileRow.user_id], set_={"last_login_at": row.created_at}
        )
    )
    try:
        if commit:
            db.commit()
        else:
            db.flush()
    except SQLAlchemyError:
        db.rollback()
        raise
    db.refresh(row)
    return IssuedSession(
        id=row.id,
        token=token,
        csrf_token=csrf_token,
        created_at=_as_utc(row.created_at),
        last_used_at=_as_utc(row.last_used_at),
        absolute_expires_at=_as_utc(row.absolute_expires_at),
        idle_expires_at=_as_utc_or_none(row.idle_expires_at),
        remember_me=row.remember_me,
    )


def authenticate_session(
    db: Session, token: str, *, now: datetime | None = None
) -> AuthenticatedSession | None:
    """Look up a valid session, removing a presented expired session immediately."""

    try:
        token_digest = _digest_opaque_token(token, prefix=_SESSION_DIGEST_PREFIX)
    except InvalidOpaqueTokenError:
        return None

    found = db.execute(
        select(AuthSessionRow, UserRow.role, UserRow.status)
        .join(UserRow, UserRow.id == AuthSessionRow.user_id)
        .where(AuthSessionRow.token_digest == token_digest)
    ).one_or_none()
    if found is None or found[0].revoked_at is not None:
        return None
    row = found[0]

    now = _utc_now(now)
    if _is_expired(row, now):
        db.delete(row)
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise
        return None
    return _authenticated_session_from_row(row, Role(found[1]), AccountStatus(found[2]))


def validate_csrf_token(session: AuthenticatedSession, csrf_token: str) -> bool:
    """Return whether a canonical CSRF value matches this session in constant time."""

    try:
        candidate_digest = _digest_opaque_token(csrf_token, prefix=_CSRF_DIGEST_PREFIX)
    except InvalidOpaqueTokenError:
        return False
    return hmac.compare_digest(session._csrf_token_digest, candidate_digest)


def recover_csrf_token(
    db: Session,
    session: AuthenticatedSession,
    *,
    csrf_encryption_key: CsrfEncryptionKey,
    now: datetime | None = None,
) -> str | None:
    """Recover the stable CSRF value or revoke a corrupt/key-mismatched session."""

    try:
        token = decrypt_csrf_token(
            session._csrf_token_ciphertext, session._token_digest, csrf_encryption_key
        )
        if not validate_csrf_token(session, token):
            raise CsrfEnvelopeError("CSRF session recovery failed")
    except CsrfEnvelopeError:
        revoke_session_by_id(db, session.id, now=now)
        return None
    return token


def revoke_sessions_with_other_csrf_key_id(
    db: Session, key_id: str, *, now: datetime | None = None, commit: bool = True
) -> int:
    """Revoke every active session that cannot be decrypted by the loaded key."""

    active_rows = db.scalars(
        select(AuthSessionRow).where(AuthSessionRow.revoked_at.is_(None))
    ).all()
    revoked_ids = [
        row.id for row in active_rows if envelope_key_id(row.csrf_token_ciphertext) != key_id
    ]
    if not revoked_ids:
        return 0
    result = db.execute(
        update(AuthSessionRow)
        .where(AuthSessionRow.id.in_(revoked_ids), AuthSessionRow.revoked_at.is_(None))
        .values(revoked_at=_database_timestamp(_utc_now(now)))
    )
    if commit:
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise
    return result.rowcount


def refresh_session_activity(
    db: Session,
    session: AuthenticatedSession,
    *,
    now: datetime | None = None,
    configuration: Settings = settings,
) -> bool:
    """Coalesce eligible activity updates without extending a session's absolute deadline.

    Callers must invoke this only after the request has fully authenticated and passed all
    applicable CSRF and Origin checks. A ``False`` return means no row write occurred;
    it does not by itself invalidate an already-authenticated request.
    """

    now = _utc_now(now)
    idle_lifetime, _ = _session_lifetimes(
        remember_me=session.remember_me, configuration=configuration
    )
    touch_interval = _effective_touch_interval(idle_lifetime)
    if now - session.last_used_at < touch_interval:
        return False

    new_idle_expiry = min(now + idle_lifetime, session.absolute_expires_at)
    database_now = _database_timestamp(now)
    result = db.execute(
        update(AuthSessionRow)
        .where(
            AuthSessionRow.id == session.id,
            AuthSessionRow.revoked_at.is_(None),
            AuthSessionRow.absolute_expires_at > database_now,
            or_(
                AuthSessionRow.idle_expires_at.is_(None),
                AuthSessionRow.idle_expires_at > database_now,
            ),
            AuthSessionRow.last_used_at <= _database_timestamp(now - touch_interval),
        )
        .values(
            last_used_at=database_now,
            idle_expires_at=_database_timestamp(new_idle_expiry),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return True


def revoke_session_by_id(db: Session, session_id: int, *, now: datetime | None = None) -> bool:
    """Revoke one session by ID without handling its bearer token."""

    result = db.execute(
        update(AuthSessionRow)
        .where(AuthSessionRow.id == session_id, AuthSessionRow.revoked_at.is_(None))
        .values(revoked_at=_database_timestamp(_utc_now(now)))
    )
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return result.rowcount == 1


def revoke_user_sessions(
    db: Session, user_id: int, *, now: datetime | None = None, commit: bool = True
) -> int:
    """Revoke every still-active session of one user and return the count changed."""

    result = db.execute(
        update(AuthSessionRow)
        .where(AuthSessionRow.user_id == user_id, AuthSessionRow.revoked_at.is_(None))
        .values(revoked_at=_database_timestamp(_utc_now(now)))
    )
    if commit:
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise
    else:
        try:
            db.flush()
        except SQLAlchemyError:
            db.rollback()
            raise
    return result.rowcount


def cleanup_expired_sessions(db: Session, *, now: datetime | None = None) -> int:
    """Delete one deterministic, bounded batch of expired or revoked sessions."""

    database_now = _database_timestamp(_utc_now(now))
    candidate_ids = list(
        db.scalars(
            select(AuthSessionRow.id)
            .where(
                or_(
                    AuthSessionRow.revoked_at.is_not(None),
                    AuthSessionRow.absolute_expires_at <= database_now,
                    AuthSessionRow.idle_expires_at <= database_now,
                )
            )
            .order_by(AuthSessionRow.id)
            .limit(_SESSION_CLEANUP_BATCH_SIZE)
        )
    )
    if not candidate_ids:
        return 0
    db.execute(delete(AuthSessionRow).where(AuthSessionRow.id.in_(candidate_ids)))
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return len(candidate_ids)


def _new_opaque_token() -> str:
    """Generate the one accepted textual representation of 256 random bits."""

    return base64.urlsafe_b64encode(secrets.token_bytes(_OPAQUE_TOKEN_BYTES)).rstrip(b"=").decode(
        "ascii"
    )


def _digest_opaque_token(value: str, *, prefix: bytes) -> str:
    """Validate a canonical opaque token before deriving its purpose-specific digest."""

    if not isinstance(value, str) or not _OPAQUE_TOKEN_PATTERN.fullmatch(value):
        raise InvalidOpaqueTokenError("Token must be canonical unpadded base64url")
    try:
        raw_value = base64.urlsafe_b64decode(f"{value}=")
    except (ValueError, UnicodeEncodeError) as error:  # pragma: no cover - regex filters bytes.
        raise InvalidOpaqueTokenError("Token must be canonical unpadded base64url") from error
    if len(raw_value) != _OPAQUE_TOKEN_BYTES:
        raise InvalidOpaqueTokenError("Token must encode exactly 32 bytes")
    if base64.urlsafe_b64encode(raw_value).rstrip(b"=").decode("ascii") != value:
        raise InvalidOpaqueTokenError("Token must be canonical unpadded base64url")
    return hashlib.sha256(prefix + raw_value).hexdigest()


def _session_lifetimes(
    *, remember_me: bool, configuration: Settings
) -> tuple[timedelta, timedelta]:
    if remember_me:
        return (
            timedelta(seconds=configuration.auth_remember_me_idle_seconds),
            timedelta(seconds=configuration.auth_remember_me_seconds),
        )
    return (
        timedelta(seconds=configuration.auth_session_idle_seconds),
        timedelta(seconds=configuration.auth_session_absolute_seconds),
    )


def _effective_touch_interval(idle_lifetime: timedelta) -> timedelta:
    return min(_SESSION_TOUCH_INTERVAL, idle_lifetime / 2)


def _is_expired(row: AuthSessionRow, now: datetime) -> bool:
    return _as_utc(row.absolute_expires_at) <= now or (
        row.idle_expires_at is not None and _as_utc(row.idle_expires_at) <= now
    )


def _authenticated_session_from_row(
    row: AuthSessionRow, role: Role, status: AccountStatus
) -> AuthenticatedSession:
    """Convert a valid persisted session row into the authenticated session view."""
    return AuthenticatedSession(
        id=row.id,
        user_id=row.user_id,
        role=role,
        status=status,
        created_at=_as_utc(row.created_at),
        last_used_at=_as_utc(row.last_used_at),
        absolute_expires_at=_as_utc(row.absolute_expires_at),
        idle_expires_at=_as_utc_or_none(row.idle_expires_at),
        remember_me=row.remember_me,
        _csrf_token_digest=row.csrf_token_digest,
        _token_digest=row.token_digest,
        _csrf_token_ciphertext=row.csrf_token_ciphertext,
    )


def _utc_now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Session timestamps must be timezone-aware UTC values")
    return value.astimezone(timezone.utc)


def _database_timestamp(value: datetime) -> datetime:
    """Store UTC consistently in SQLite's timezone-naive ``DateTime`` columns."""

    return _utc_now(value).replace(tzinfo=None)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_utc_or_none(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None
