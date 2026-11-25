"""A short per-account cooldown for manual Find and update checks (local API §2, §8).

The same account may Find the same target and label, or check the same link, at
most once per `seconds`. A repeat inside the window is `429 rate_limited` with
`retryAfter`; it never reaches the community service.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Hashable

from scanner.integrations.community.transport import CommunityFailure

COOLDOWN_SECONDS = 10.0


class Cooldown:
    """An in-process map of `(account, action, subject)` to when it may run again."""

    def __init__(self, seconds: float = COOLDOWN_SECONDS, *, clock=time.monotonic):
        self.seconds = seconds
        self.clock = clock
        self.lock = threading.Lock()
        self.until: dict[Hashable, float] = {}

    def claim(self, key: Hashable) -> None:
        """Start the window for `key`, or raise `rate_limited` while it is open."""
        with self.lock:
            now = self.clock()
            self.until = {k: v for k, v in self.until.items() if v > now}
            if key in self.until:
                wait = max(1, math.ceil(self.until[key] - now))
                raise CommunityFailure("rate_limited", retryable=True, details={"retryAfter": wait})
            if self.seconds > 0:
                self.until[key] = now + self.seconds


cooldowns = Cooldown()
