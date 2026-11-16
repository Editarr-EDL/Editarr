"""Retry provider evidence when its stored authority changes before a write."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import TypeVar

from sqlalchemy.orm import Session

from scanner.persistence.write_lock import write_lock

MAX_EVIDENCE_ATTEMPTS = 3
State = TypeVar("State")
Evidence = TypeVar("Evidence")


@contextmanager
def fresh_authority_write(
    db: Session,
    prepare: Callable[[], tuple[State, Evidence]],
    state: Callable[[], State],
    exhausted: Callable[[], Exception],
    *,
    invalidate: Callable[[], None] | None = None,
    lock: Callable[[Session], AbstractContextManager] = write_lock,
) -> Iterator[Evidence]:
    """Collect evidence unlocked, compare its authority locked, and commit one operation."""
    for _attempt in range(MAX_EVIDENCE_ATTEMPTS):
        before, evidence = prepare()
        with lock(db):
            if state() != before:
                db.rollback()
                if invalidate is not None:
                    invalidate()
                continue
            yield evidence
            db.commit()
            return
    raise exhausted()
