"""Serialize a SQLite write with the reads it decides on."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session


@contextmanager
def write_lock(db: Session) -> Iterator[None]:
    """Hold SQLite's write lock over a write and every read it decides on.

    Begins with BEGIN IMMEDIATE unless writes the caller already staged hold the
    lock, and drops rows loaded before it. No other writer commits until the caller
    commits or rolls back; a raised refusal rolls back.
    """
    db.flush()
    if not db.connection().connection.driver_connection.in_transaction:
        db.execute(text("BEGIN IMMEDIATE"))
    db.expire_all()
    try:
        yield
    except BaseException:
        db.rollback()
        raise
