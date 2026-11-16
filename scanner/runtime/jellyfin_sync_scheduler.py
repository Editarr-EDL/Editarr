"""Run the Jellyfin account sync every five minutes in a background thread.

`tick` decides from the injected clock whether a run is due, so cadence tests
use a fixed clock and never sleep. The thread only polls `tick`. `stop` cancels
the run in progress and waits for its thread to end.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from scanner.integrations.jellyfin_user_transport import ResponseLimits
from scanner.security.jellyfin_accounts.sync import (
    SYNC_INTERVAL,
    AdapterFactory,
    SyncInProgress,
    SyncRegistry,
    SyncRun,
    SyncTrigger,
    open_adapter,
    registry,
)

logger = logging.getLogger(__name__)

DRAIN_SECONDS = ResponseLimits().seconds + 5
"""How long `stop` waits: one Jellyfin exchange and the write. A verification can
make several exchanges, so a cancelled run may outlast it, writing nothing."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class JellyfinSyncScheduler:
    """Start a sync when the last scheduled one started `interval` ago, the first at once."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] = _utc_now,
        interval: timedelta = SYNC_INTERVAL,
        adapters: AdapterFactory = open_adapter,
        sync: SyncRegistry = registry,
        poll_seconds: float = 15.0,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock
        self.interval = interval
        self.adapters = adapters
        self.sync = sync
        self.poll_seconds = poll_seconds
        self.next_due: datetime | None = None
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="jellyfin-sync", daemon=True)

    def tick(self) -> SyncRun | None:
        """Run the sync if it is due; a run already in progress counts as this tick's run."""

        now = self.clock()
        if self.next_due is not None and now < self.next_due:
            return None
        self.next_due = now + self.interval
        db = self.session_factory()
        try:
            return self.sync.run(
                db,
                SyncTrigger.SCHEDULED,
                clock=self.clock,
                adapters=self.adapters,
                cancelled=self.stop_event,
            )
        except SyncInProgress:
            return None
        finally:
            db.close()

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Scheduled Jellyfin account sync failed")
            self.stop_event.wait(self.poll_seconds)

    def start(self) -> None:
        self.thread.start()

    def stop(self, timeout: float = DRAIN_SECONDS) -> bool:
        """Cancel the run in progress and wait up to `timeout` seconds for the thread to end.

        A cancelled run never writes again. Returns False while its thread is
        still finishing a Jellyfin read; the caller keeps the scheduler then.
        """

        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout)
        return not self.thread.is_alive()
