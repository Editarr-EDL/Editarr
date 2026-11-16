"""Run one startup sweep against a test database and hard-exit at a named cleanup point.

`python -m tests.account_link_returned_crash DATABASE POINT` runs `sweep_regenerations`,
which finishes open account links and reconciles settled old paths, and calls
`os._exit(97)` while it removes owned old bytes that returned: `before_intent` (bytes
recognized, nothing committed), `after_intent` (any requeue committed, file still
there), `after_unlink`, `before_commit` (outcome staged), or `after_commit`.
Exits 0 when POINT is never reached.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from scanner.db import initialize_sqlite_connection
from scanner.persistence.repository import export_identity_moves as files
from scanner.persistence.repository.export_regeneration import sweep_regenerations
from scanner.security.account_links import transition


def main(database: str, point: str) -> None:
    def stop(at: str) -> None:
        if at == point:
            os._exit(97)

    real_holds, real_remove, real_settle = files.holds_owned, files.remove_old, transition._settle
    staged = []

    def holds_owned(path, expected):
        owned = real_holds(path, expected)
        if owned:
            stop("before_intent")
        return owned

    def remove_old(path, expected):
        if files._recognize(path, expected) == "owned":
            stop("after_intent")
        outcome = real_remove(path, expected)
        if outcome == "removed":
            stop("after_unlink")
        return outcome

    def settle(db, move, outcome):
        real_settle(db, move, outcome)
        if outcome == "removed":
            stop("before_commit")
            staged.append(outcome)

    def committed(_session):
        if staged:
            stop("after_commit")

    files.holds_owned, files.remove_old, transition._settle = holds_owned, remove_old, settle
    event.listen(Session, "after_commit", committed)
    engine = create_engine(f"sqlite:///{database}")
    event.listen(engine, "connect", initialize_sqlite_connection)
    with sessionmaker(bind=engine)() as db:
        sweep_regenerations(db)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
