"""Generate, wrap, reveal, and verify each user's one local integration credential."""

import hashlib
import hmac
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from scanner.db import ApiKeyRow, UserRow
from scanner.security.access.identity import AccountStatus

from .api_key_crypto import (
    ApiKeyEnvelopeError,
    ApiKeyWrapKey,
    load_api_key_wrap_key,
    open_api_key,
    seal_api_key,
)

_KEY = re.compile(r"[0-9a-f]{64}")
_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KeyHolder:
    """The account behind one presented API key and its current status."""

    user_id: int
    status: AccountStatus


@dataclass(frozen=True)
class StoredKey:
    """A user's key as stored: `value` is None when only its digest survives.

    That happens for a digest-only key from before revision 0009 and for a key
    wrapped under a wrap key this installation no longer has.
    """

    created_at: datetime
    value: str | None


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def rotate_api_key(
    db: Session,
    user_id: int,
    wrap_key: ApiKeyWrapKey,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> str:
    """Atomically replace one user's key, keeping only its digest and wrapped copy."""

    key = secrets.token_hex(32)
    digest = _digest(key)
    values = {
        "token_digest": digest,
        "token_value": None,
        "secret_envelope": seal_api_key(key, digest, wrap_key),
        "created_at": now or datetime.now(timezone.utc),
    }
    db.execute(
        insert(ApiKeyRow)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(index_elements=[ApiKeyRow.user_id], set_=values)
    )
    if commit:
        db.commit()
    return key


def stored_api_key(db: Session, user_id: int, wrap_key: ApiKeyWrapKey) -> StoredKey | None:
    """Return one user's key with its value recovered when possible, or None without a key."""

    row = db.execute(
        select(ApiKeyRow.created_at, ApiKeyRow.token_digest, ApiKeyRow.secret_envelope).where(
            ApiKeyRow.user_id == user_id
        )
    ).one_or_none()
    if row is None:
        return None
    created_at, digest, envelope = row
    try:
        value = open_api_key(envelope, digest, wrap_key) if envelope is not None else None
    except ApiKeyEnvelopeError:
        value = None
    return StoredKey(created_at, value)


def revoke_api_key(db: Session, user_id: int, *, commit: bool = True) -> bool:
    """Delete one user's key without touching browser sessions or other users' keys.

    Returns whether a key existed.
    """
    deleted = db.execute(delete(ApiKeyRow).where(ApiKeyRow.user_id == user_id)).rowcount
    if commit:
        db.commit()
    return deleted > 0


def seal_plaintext_api_keys(db: Session, wrap_key: ApiKeyWrapKey) -> int:
    """Replace every pre-0015 plaintext value with its wrapped copy; returns rows changed.

    Safe to repeat: a sealed row has no plaintext left. A value that does not
    match its digest could never authenticate, so it is dropped, not wrapped.
    A marker beside the database, written before the rows commit, keeps the
    file cleanup pending until a later call completes it.
    """

    rows = db.execute(
        select(ApiKeyRow.id, ApiKeyRow.token_digest, ApiKeyRow.token_value).where(
            ApiKeyRow.token_value.is_not(None)
        )
    ).all()
    marker = _cleanup_marker(db)
    if rows and marker is not None:
        _mark_cleanup_pending(marker)
    for row_id, digest, value in rows:
        matches = _KEY.fullmatch(value) is not None and hmac.compare_digest(_digest(value), digest)
        # Never bind the plaintext: a failed statement's error text includes its parameters.
        db.execute(
            update(ApiKeyRow)
            .where(ApiKeyRow.id == row_id, ApiKeyRow.token_value.is_not(None))
            .values(
                token_value=None,
                secret_envelope=seal_api_key(value, digest, wrap_key) if matches else None,
            )
        )
    db.commit()
    if marker is not None and marker.exists() and _scrub_free_pages(db):
        marker.unlink(missing_ok=True)
    return len(rows)


def _cleanup_marker(db: Session) -> Path | None:
    """Where a file database records pending cleanup; None for an in-memory database."""
    database = db.get_bind().engine.url.database
    if not database or database == ":memory:":
        return None
    path = Path(database)
    return path.with_name(path.name + ".api-key-cleanup")


def _mark_cleanup_pending(marker: Path) -> None:
    os.close(os.open(marker, os.O_WRONLY | os.O_CREAT, 0o600))
    if os.name == "posix":
        directory = os.open(marker.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _scrub_free_pages(db: Session) -> bool:
    """Rewrite the database file so no free page or WAL frame keeps replaced plaintext.

    Older revisions left key plaintext in pages freed by their own table
    rebuilds. Returns True only after VACUUM and a complete truncating
    checkpoint; a reader holding an old snapshot or a full disk returns False.
    """

    try:
        with db.get_bind().connect().execution_options(isolation_level="AUTOCOMMIT") as raw:
            raw.exec_driver_sql("VACUUM")
            busy, log, checkpointed = raw.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").one()
        if busy == 0 and log == checkpointed:
            return True
    except OperationalError:
        pass
    _logger.warning("Sealed API keys, but could not rewrite the database file yet")
    return False


def seal_database_api_keys(database: Path, data_dir: Path) -> int:
    """Seal plaintext keys in the database file at `database` with this installation's wrap key.

    For callers outside a request, such as restore activation after it switched files.
    """

    engine = create_engine(f"sqlite:///{database}", poolclass=NullPool)
    try:
        with Session(engine) as db:
            return seal_plaintext_api_keys(db, load_api_key_wrap_key(data_dir))
    finally:
        engine.dispose()


def authenticate_api_key(db: Session, candidate: str) -> KeyHolder | None:
    """Return the holder of a well-formed matching key, compared in constant time."""
    if _KEY.fullmatch(candidate) is None:
        return None
    digest = _digest(candidate)
    found = db.execute(
        select(ApiKeyRow.token_digest, UserRow.id, UserRow.status)
        .join(UserRow, UserRow.id == ApiKeyRow.user_id)
        .where(ApiKeyRow.token_digest == digest)
    ).one_or_none()
    if found is None or not hmac.compare_digest(found[0], digest):
        return None
    return KeyHolder(user_id=found[1], status=AccountStatus(found[2]))
