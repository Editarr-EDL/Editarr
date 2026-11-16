"""Refresh linked accounts from Jellyfin: at sign-in, every five minutes, and on demand.

One run verifies every linked account with one adapter for the configured
server and stores each transition. Runs never overlap: a manual request during
a run is refused with `SyncInProgress`, and the scheduler skips its tick.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy.orm import Session

from scanner.integrations.jellyfin import JellyfinError
from scanner.integrations.jellyfin_user_snapshot import JellyfinUserSnapshot
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.persistence.repository.repository_settings import get_jellyfin_credentials
from scanner.security.access import link_state

from . import reads, store
from .admission import fetch_avatar
from .states import Disconnected, SyncOutcome

SYNC_INTERVAL = timedelta(minutes=5)


class SyncTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class SyncInProgress(Exception):
    """Another sync run holds the lock."""


@dataclass(frozen=True)
class SyncRun:
    trigger: SyncTrigger
    started_at: datetime
    finished_at: datetime
    results: tuple[store.SyncResult, ...]

    def counts(self) -> dict[str, int]:
        found = {outcome.value: 0 for outcome in SyncOutcome}
        for result in self.results:
            found[result.outcome.value] += 1
        return found


def open_adapter(db: Session) -> JellyfinUserAdapter | None:
    """An adapter for the configured Jellyfin connection, or None when none is configured."""

    credentials = get_jellyfin_credentials(db)
    if credentials is None:
        return None
    try:
        # Verification times follow the clock account loads judge them by.
        return JellyfinUserAdapter(*credentials, clock=lambda: link_state.link_clock())
    except JellyfinError:
        return None


AdapterFactory = Callable[[Session], JellyfinUserAdapter | None]


def sync_accounts(
    db: Session,
    adapter: JellyfinUserAdapter | None,
    *,
    clock: Callable[[], datetime],
    user_ids: Iterable[int] | None = None,
    cancelled: threading.Event | None = None,
) -> list[store.SyncResult]:
    """Verify each linked account (or the listed ones) and store its transition.

    Each account's read takes its generation and time from `clock` as it
    begins. Once `cancelled` is set the run stops before its next read and
    writes nothing more; a read already in flight finishes first. A read in
    another epoch than the run's first ends the run: a restore replaced the
    accounts it listed.
    """

    wanted = None if user_ids is None else set(user_ids)
    stopped = cancelled.is_set if cancelled is not None else lambda: False
    results = []
    epoch = None
    for record in store.linked_accounts(db):
        if wanted is not None and record.account.user_id not in wanted:
            continue
        try:
            read = reads.begin_read(db, clock, cancelled=cancelled)
        except reads.ReadCancelled:
            break
        if epoch is not None and read.epoch != epoch:
            break
        epoch = read.epoch
        evidence = Disconnected() if adapter is None else adapter.verify_user(record.identity)
        if stopped():
            break
        avatar = None
        if adapter is not None and isinstance(evidence, JellyfinUserSnapshot):
            avatar = fetch_avatar(adapter, evidence, record.avatar_tag)
        db.rollback()
        result = store.apply(
            db,
            record.account.user_id,
            evidence,
            read,
            avatar,
            cancelled=cancelled,
            identity=record.identity,
        )
        if result is not None:
            results.append(result)
    return results


@dataclass
class SyncRegistry:
    """The process's one sync lock and its last finished run."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    last_run: SyncRun | None = None

    @property
    def running(self) -> bool:
        return self.lock.locked()

    def run(
        self,
        db: Session,
        trigger: SyncTrigger,
        *,
        clock: Callable[[], datetime],
        adapters: AdapterFactory = open_adapter,
        user_ids: Iterable[int] | None = None,
        cancelled: threading.Event | None = None,
    ) -> SyncRun:
        """Run one sync unless another is running; raises `SyncInProgress` then.

        Results leave out accounts removed during the run and accounts a newer
        read already changed.
        """

        if not self.lock.acquire(blocking=False):
            raise SyncInProgress
        try:
            started = clock()
            adapter = adapters(db)
            try:
                results = sync_accounts(
                    db, adapter, clock=clock, user_ids=user_ids, cancelled=cancelled
                )
            finally:
                if adapter is not None:
                    adapter.close()
            run = SyncRun(trigger, started, clock(), tuple(results))
            self.last_run = run
            return run
        finally:
            self.lock.release()


registry = SyncRegistry()
