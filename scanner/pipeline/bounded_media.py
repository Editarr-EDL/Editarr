"""Cancellable media input with aggregate probe/seek read limits."""

import time
from pathlib import Path


class MediaBudgetError(ValueError):
    pass


class BoundedInput:
    """Cap actual reads and seeks; check elapsed time at each I/O boundary."""

    def __init__(
        self,
        path: Path,
        should_cancel=None,
        *,
        max_bytes: int | None = 16 * 1024 * 1024,
        max_seconds: float | None = 10,
        max_seeks: int | None = 128,
    ):
        """Default to probe limits; use None to disable a budget for streaming analysis."""
        self.source = path.open("rb")
        self.bytes_read = 0
        self.read_calls = 0
        self.seeks = 0
        self.started = time.monotonic()
        self.should_cancel = should_cancel
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds
        self.max_seeks = max_seeks

    def check(self):
        if self.should_cancel and self.should_cancel():
            raise MediaBudgetError("cancelled")
        if self.max_seconds is not None and time.monotonic() - self.started > self.max_seconds:
            raise MediaBudgetError("wall_budget")

    def read(self, size=-1):
        self.check()
        if self.max_bytes is not None and (size < 0 or size > self.max_bytes - self.bytes_read):
            raise MediaBudgetError("read_budget")
        payload = self.source.read(size)
        self.bytes_read += len(payload)
        self.read_calls += 1
        return payload

    def seek(self, offset, whence=0):
        self.check()
        self.seeks += 1
        if self.max_seeks is not None and self.seeks > self.max_seeks:
            raise MediaBudgetError("seek_budget")
        return self.source.seek(offset, whence)

    def tell(self):
        return self.source.tell()

    def readable(self):
        return True

    def seekable(self):
        return True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.source.close()
