"""Recognize SQLite giving up on another connection's lock."""

import sqlite3

_BUSY_CODES = frozenset({5, 6})  # SQLITE_BUSY, SQLITE_LOCKED


def is_busy(error: BaseException) -> bool:
    """Whether `error` (or the driver error SQLAlchemy wraps) is an exhausted busy wait.

    Such a failure says nothing about the request, so callers answer it as
    retryable instead of as a server error or a rejected credential.
    """

    original = getattr(error, "orig", error)
    return (
        isinstance(original, sqlite3.OperationalError)
        and getattr(original, "sqlite_errorcode", 0) & 0xFF in _BUSY_CODES
    )
