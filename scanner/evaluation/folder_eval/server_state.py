"""Shared state for the review server: inventory lookup and the debounced summarizer."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .paths import Layout

log = logging.getLogger(__name__)


class InventoryIndex:
    """Item id → inventory `expected`, reloaded when inventory.json changes on disk."""

    def __init__(self, layout: Layout) -> None:
        self._layout = layout
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._expected: dict[str, list[str]] = {}

    def expected(self) -> dict[str, list[str]]:
        with self._lock:
            mtime = self._layout.inventory.stat().st_mtime
            if mtime != self._mtime:
                payload = json.loads(self._layout.inventory.read_text(encoding="utf-8"))
                self._expected = {item["id"]: item["expected"] for item in payload["items"]}
                self._mtime = mtime
            return self._expected


class Summarizer:
    """Runs `job` on a single background thread, debounced; requests during a run
    schedule exactly one follow-up run, so two never overlap. `stop()` cancels pending
    work, waits for an active run and joins the thread."""

    def __init__(self, job: Callable[[], Any], delay_s: float = 2.0) -> None:
        self._job = job
        self._delay = delay_s
        self._cond = threading.Condition()
        self._due: float | None = None
        self._running = False
        self._stopped = False
        self.last_error: str | None = None
        self._thread = threading.Thread(target=self._loop, name="summarizer", daemon=True)
        self._thread.start()

    @property
    def busy(self) -> bool:
        with self._cond:
            return self._running or self._due is not None

    def request(self) -> None:
        with self._cond:
            if self._stopped:
                return
            self._due = time.monotonic() + self._delay
            self._cond.notify()

    def stop(self, timeout_s: float | None = None) -> None:
        """Cancel any pending run, let an active run finish, and join the thread."""
        with self._cond:
            self._stopped = True
            self._due = None
            self._cond.notify_all()
        self._thread.join(timeout_s)

    def wait_idle(self, timeout_s: float = 30.0) -> bool:
        """Block until no run is pending or active (used by tests)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self.busy:
                return True
            time.sleep(0.05)
        return False

    def _next_run(self) -> bool:
        """Wait until a run is due; False once stopped."""
        with self._cond:
            while not self._stopped and (self._due is None or time.monotonic() < self._due):
                if self._due is None:
                    self._cond.wait()
                else:
                    self._cond.wait(max(0.0, self._due - time.monotonic()))
            if self._stopped:
                return False
            self._due = None
            self._running = True
            return True

    def _loop(self) -> None:
        while self._next_run():
            started = time.monotonic()
            try:
                self._job()
                self.last_error = None
                log.info("summary refreshed in %.1fs", time.monotonic() - started)
            except Exception as exc:  # noqa: BLE001 - keep serving; surface via status
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("background summarize failed")
            finally:
                with self._cond:
                    self._running = False
