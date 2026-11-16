"""Bounded two-principal login abuse protection."""

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil

from scanner.settings.config import Settings, settings

_MAX_LOGIN_LIMITER_BUCKETS = 4_096


@dataclass(slots=True)
class _LoginBucket:
    failures: deque[float]
    refusal_reported: bool = False


class LoginRateLimiter:
    """A bounded, deterministic two-bucket sliding-window login limiter."""

    def __init__(
        self,
        *,
        configuration: Settings = settings,
        clock: Callable[[], float] = time.monotonic,
        maximum_buckets: int = _MAX_LOGIN_LIMITER_BUCKETS,
    ) -> None:
        self._configuration = configuration
        self._clock = clock
        self._maximum_buckets = maximum_buckets
        self._username_buckets: OrderedDict[str, _LoginBucket] = OrderedDict()
        self._address_buckets: OrderedDict[str, _LoginBucket] = OrderedDict()

    def allowed(self, *, username: str, address: str) -> bool:
        return self.retry_after(username=username, address=address) == 0

    def retry_after(self, *, username: str, address: str) -> int:
        """Seconds until both sliding-window buckets admit an attempt, rounded up."""
        return max(
            self._wait(
                self._username_buckets, username, self._configuration.auth_login_username_attempts
            ),
            self.address_retry_after(address),
        )

    def address_retry_after(self, address: str) -> int:
        """Seconds until `address`'s bucket admits an attempt, rounded up."""
        return self._wait(
            self._address_buckets, address, self._configuration.auth_login_address_attempts
        )

    def _wait(self, buckets: OrderedDict[str, _LoginBucket], key: str, limit: int) -> int:
        now = self._clock()
        failures = self._active_failures(buckets, key, now)
        if len(failures) < limit:
            return 0
        return max(0, ceil(failures[-limit] + self._configuration.auth_login_window_seconds - now))

    def record_failure(self, *, username: str, address: str) -> None:
        now = self._clock()
        self._record(self._username_buckets, username, now)
        self._record(self._address_buckets, address, now)

    def record_address_failure(self, address: str) -> None:
        """Count a refusal that says nothing about the typed name against `address` only."""
        self._record(self._address_buckets, address, self._clock())

    def first_refusal(self, *, username: str, address: str) -> bool:
        """Mark each full bucket as reported; true only for a lockout not reported before.

        Call after `allowed` returns false. A bucket forgets the mark when its
        failures age out, so the next lockout is reported again.
        """

        first = False
        for buckets, key, limit in (
            (self._username_buckets, username, self._configuration.auth_login_username_attempts),
            (self._address_buckets, address, self._configuration.auth_login_address_attempts),
        ):
            bucket = buckets.get(key)
            if bucket is not None and len(bucket.failures) >= limit and not bucket.refusal_reported:
                bucket.refusal_reported = True
                first = True
        return first

    def clear_username(self, username: str) -> None:
        self._username_buckets.pop(username, None)

    def clear(self) -> None:
        self._username_buckets.clear()
        self._address_buckets.clear()

    def _active_failures(
        self, buckets: OrderedDict[str, _LoginBucket], key: str, now: float
    ) -> deque[float]:
        """Count recent failed authentication attempts for the supplied principal and client."""
        bucket = buckets.get(key)
        if bucket is None:
            self._prune(buckets, now)
            return deque()
        self._discard_expired(bucket.failures, now)
        if not bucket.failures:
            buckets.pop(key, None)
            return deque()
        buckets.move_to_end(key)
        self._prune(buckets, now)
        return bucket.failures

    def _record(self, buckets: OrderedDict[str, _LoginBucket], key: str, now: float) -> None:
        failures = self._active_failures(buckets, key, now)
        if key not in buckets:
            buckets[key] = _LoginBucket(failures)
        failures.append(now)
        # A counted failure means the bucket was open, so a later lockout is a new one.
        buckets[key].refusal_reported = False
        buckets.move_to_end(key)
        self._prune(buckets, now)
        while len(buckets) > self._maximum_buckets:
            buckets.popitem(last=False)

    def _prune(self, buckets: OrderedDict[str, _LoginBucket], now: float) -> None:
        for key in tuple(buckets):
            self._discard_expired(buckets[key].failures, now)
            if not buckets[key].failures:
                buckets.pop(key)

    def _discard_expired(self, failures: deque[float], now: float) -> None:
        cutoff = now - self._configuration.auth_login_window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
