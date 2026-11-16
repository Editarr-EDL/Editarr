"""Application-lifetime interval runner; no operating-system scheduler or wakeup."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .schedule import run_due


class BackupScheduler:
    def __init__(self, database: Path, data_dir: Path):
        self.database = database
        self.data_dir = data_dir
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="backup-schedule", daemon=True)

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                run_due(self.database, self.data_dir)
            except Exception:
                logging.getLogger(__name__).warning("Scheduled backup state is unavailable")
            self.stop_event.wait(30)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join()
