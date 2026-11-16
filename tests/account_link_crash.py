"""Run one account link against a test database and hard-exit at a named point.

`python -m tests.account_link_crash DATABASE USER_ID POINT` links USER_ID to the
synthetic Jellyfin user Drew with the same steps as `POST /api/users/{id}/link`,
calling `os._exit(97)` at POINT: `after_begin`, `after_staging` (a staging file is
fsynced, its identity not yet recorded), `after_record` (the identity is committed,
the file not yet placed), `after_place` (the file is at its new path and still at
the staging name), `mid_write` (the first file is placed), `after_write`,
`after_switch`, or `mid_remove`. Exits 0 when POINT is never reached.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from scanner.db import initialize_sqlite_connection
from scanner.formats.export_layout import JellyfinUserId
from scanner.integrations.jellyfin_user_snapshot import JellyfinUserSnapshot, MediaPolicy
from scanner.persistence.repository import export_identity_moves as files
from scanner.persistence.repository.export_recovery import publication_lock
from scanner.persistence.write_lock import write_lock
from scanner.security.access.identity import JellyfinServerId, ProviderIdentity
from scanner.security.account_links import transition
from scanner.security.account_links.plan import build_plan
from scanner.security.account_links.proofs import read_binding
from scanner.security.jellyfin_accounts import reads
from tests.jellyfin_user_boundary import IDS, PRIMARY

ALL_LIBRARIES = MediaPolicy(True, frozenset(), None, None, (), (), frozenset())


def snapshot(now: datetime) -> JellyfinUserSnapshot:
    return JellyfinUserSnapshot(
        identity=ProviderIdentity(JellyfinServerId(PRIMARY), JellyfinUserId(IDS["Drew"])),
        name="Drew",
        avatar_tag=None,
        is_administrator=False,
        is_disabled=False,
        media=ALL_LIBRARIES,
        verified_at=now,
    )


def main(database: Path, user_id: int, point: str) -> None:
    def stop(at: str) -> None:
        if at == point:
            os._exit(97)

    engine = create_engine(f"sqlite:///{database}")
    event.listen(engine, "connect", initialize_sqlite_connection)
    db = sessionmaker(bind=engine)()
    now = datetime.now(UTC)
    evidence = snapshot(now)
    read = reads.begin_read(db, lambda: now)
    real_remove = files.remove_old

    def remove_then_stop(*args):
        outcome = real_remove(*args)
        stop("mid_remove")
        return outcome

    files.remove_old = remove_then_stop
    real_temporary = files._temporary

    def stage_then_stop(*args):
        staged = real_temporary(*args)
        stop("after_staging")
        return staged

    files._temporary = stage_then_stop
    real_place_new, real_place = files.place_new, files._place

    def place_then_stop(new_path):
        stop("after_record")
        real_place_new(new_path)
        stop("mid_write")

    def link_then_stop(*args):
        real_place(*args)
        stop("after_place")

    files.place_new, files._place = place_then_stop, link_then_stop
    with publication_lock(db):
        with write_lock(db):
            plan = build_plan(db, user_id, evidence)
            assert not plan.blockers, plan.blockers
            binding = read_binding(db, user_id, evidence.identity)
            transition_id = transition.begin(db, plan, 2, now).id
            db.commit()
        stop("after_begin")
        transition.write_files(db, transition_id, plan)
        stop("after_write")
        with write_lock(db):
            transition.switch(db, transition_id, plan, evidence, read, None, binding)
            db.commit()
        stop("after_switch")
        transition.finish(db, transition_id, now)


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3])
