"""Pending Jellyfin Quick Connect sign-ins, held in server memory, one per browser.

The browser holds only an opaque handle, in an HttpOnly cookie, and the
registry keys each attempt by the handle's SHA-256. Jellyfin's secret never
leaves the registry: Jellyfin 12 accepts an approved secret again until the
request expires, so `claim` hands it out once and the attempt is then spent.
Attempts do not survive a restart, which the browser sees as an expired code.
Starting attempts is limited per client network and across all clients, and
an open attempt is never dropped to make room for another.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from ipaddress import ip_address, ip_network
from math import ceil

from scanner.integrations.jellyfin_quick_connect import QuickConnectRequest


class QuickConnectState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    CONSUMED = "consumed"


_S = QuickConnectState
_NEXT: dict[QuickConnectState, frozenset[QuickConnectState]] = {
    _S.PENDING: frozenset({_S.APPROVED, _S.EXPIRED, _S.CANCELLED}),
    _S.APPROVED: frozenset({_S.CONSUMED, _S.EXPIRED, _S.CANCELLED}),
    _S.EXPIRED: frozenset(),
    _S.CANCELLED: frozenset(),
    _S.CONSUMED: frozenset(),
}
"""Every allowed transition. The last three states are terminal."""


@dataclass(frozen=True)
class QuickConnectAttempt:
    """One browser's Quick Connect sign-in. A terminal attempt has dropped its secret."""

    code: str
    state: QuickConnectState
    secret: str | None
    started: float
    asked: float
    """When Jellyfin was last asked about this attempt."""

    def __repr__(self) -> str:
        return f"QuickConnectAttempt(code={self.code!r}, state={self.state.value})"

    @property
    def open(self) -> bool:
        return bool(_NEXT[self.state])


@dataclass(frozen=True)
class StartRefused:
    """A start limit refused a client for `retry_after` seconds.

    `first` is true for the first refusal since that limit's starts last aged out.
    """

    retry_after: int
    first: bool


@dataclass
class _Starts:
    times: deque[float] = field(default_factory=deque)
    refused: bool = False

    def refusal(self, limit: int, window: float, now: float) -> StartRefused | None:
        while self.times and self.times[0] <= now - window:
            self.times.popleft()
        if not self.times:
            self.refused = False
        if len(self.times) < limit:
            return None
        first, self.refused = not self.refused, True
        return StartRefused(max(1, ceil(self.times[0] + window - now)), first)


class QuickConnectAttempts:
    """Thread-safe registry of attempts; each lives at most `lifetime` seconds.

    Jellyfin expires a request ten minutes after it starts or is approved, so
    the default lifetime outlasts Jellyfin's and Jellyfin decides expiry. Ended
    attempts stay until their lifetime passes, so a replayed handle reads its
    final state instead of an unknown one, unless their slot is needed. The
    default start limits allow fewer starts per lifetime than there are slots.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        lifetime: float = 30 * 60,
        capacity: int = 1024,
        starts_per_window: int = 10,
        window: float = 10 * 60,
        starts_per_minute: int = 30,
        ask_interval: float = 1.0,
    ) -> None:
        self._clock = clock
        self._lifetime = lifetime
        self._capacity = capacity
        self._starts_per_window = starts_per_window
        self._window = window
        self._starts_per_minute = starts_per_minute
        self._ask_interval = ask_interval
        self._attempts: OrderedDict[str, QuickConnectAttempt] = OrderedDict()
        self._starts: OrderedDict[str, _Starts] = OrderedDict()
        self._all_starts = _Starts()
        self._lock = threading.Lock()

    def admit(self, address: str) -> StartRefused | None:
        """Record a start by the client at `address`, or refuse it under a start limit."""
        now, network = self._clock(), client_network(address)
        with self._lock:
            starts = self._starts.pop(network, None) or _Starts()
            self._starts[network] = starts
            refused = starts.refusal(self._starts_per_window, self._window, now) or (
                self._all_starts.refusal(self._starts_per_minute, 60, now)
            )
            if refused:
                return refused
            starts.times.append(now)
            self._all_starts.times.append(now)
            while len(self._starts) > self._capacity:
                self._starts.popitem(last=False)
            return None

    def begin(self, request: QuickConnectRequest, replacing: str | None = None) -> str | None:
        """Hold `request` as a pending attempt and return the browser's new handle.

        The attempt named by `replacing`, the browser's previous handle, is
        cancelled. None means every slot holds an open attempt.
        """
        handle = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._prune(now)
            if replacing is not None:
                self._move(_key(replacing), _S.CANCELLED)
            if not self._has_room():
                return None
            self._attempts[_key(handle)] = QuickConnectAttempt(
                request.code, _S.PENDING, request.secret, now, now
            )
        return handle

    def current(self, handle: str) -> QuickConnectAttempt | None:
        with self._lock:
            self._prune(self._clock())
            return self._attempts.get(_key(handle))

    def due(self, handle: str) -> QuickConnectAttempt | None:
        """The open attempt, if Jellyfin may be asked about it again now; marks it asked.

        Each attempt is asked about at most once per `ask_interval`, however
        often its browser polls.
        """
        now = self._clock()
        with self._lock:
            key = _key(handle)
            attempt = self._attempts.get(key)
            if attempt is None or not attempt.open or now - attempt.asked < self._ask_interval:
                return None
            self._attempts[key] = replace(attempt, asked=now)
            return attempt

    def observe(
        self, handle: str, request: QuickConnectRequest | None
    ) -> QuickConnectAttempt | None:
        """Apply what Jellyfin reported for the attempt; None means Jellyfin no longer knows it."""
        if request is None:
            state = _S.EXPIRED
        else:
            state = _S.APPROVED if request.authenticated else _S.PENDING
        with self._lock:
            return self._move(_key(handle), state)

    def claim(self, handle: str) -> str | None:
        """Spend an approved attempt and return its secret, once; None otherwise."""
        with self._lock:
            attempt = self._attempts.get(_key(handle))
            if attempt is None or attempt.state is not _S.APPROVED:
                return None
            secret = attempt.secret
            self._move(_key(handle), _S.CONSUMED)
            return secret

    def cancel(self, handle: str) -> QuickConnectAttempt | None:
        with self._lock:
            return self._move(_key(handle), _S.CANCELLED)

    def _move(self, key: str, state: QuickConnectState) -> QuickConnectAttempt | None:
        attempt = self._attempts.get(key)
        if attempt is None or state not in _NEXT[attempt.state]:
            return attempt
        terminal = not _NEXT[state]
        moved = replace(attempt, state=state, secret=None if terminal else attempt.secret)
        self._attempts[key] = moved
        return moved

    def _has_room(self) -> bool:
        """Drop ended attempts, oldest first, until a slot is free; False if all are open."""
        if len(self._attempts) < self._capacity:
            return True
        for key in [key for key, attempt in self._attempts.items() if not attempt.open]:
            del self._attempts[key]
            if len(self._attempts) < self._capacity:
                return True
        return False

    def _prune(self, now: float) -> None:
        while self._attempts:
            key, oldest = next(iter(self._attempts.items()))
            if oldest.started > now - self._lifetime:
                return
            del self._attempts[key]


class RecentAnswer:
    """A yes-or-no answer from one ask, shared by every caller for `ttl` seconds.

    `ask` returns None when it could not get an answer; callers then read no for
    `failed_ttl` seconds. Callers that arrive while an ask is in flight wait for
    it, so there is at most one ask at a time.
    """

    def __init__(
        self,
        ttl: float,
        *,
        failed_ttl: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl
        self._failed_ttl = failed_ttl
        self._clock = clock
        self._answer = False
        self._until = float("-inf")
        self._asking = False
        self._changed = threading.Condition()

    def get(self, ask: Callable[[], bool | None]) -> bool:
        with self._changed:
            self._changed.wait_for(lambda: not self._asking)
            if self._clock() < self._until:
                return self._answer
            self._asking = True
        answer = None
        try:
            answer = ask()
            return bool(answer)
        finally:
            with self._changed:
                self._answer = bool(answer)
                ttl = self._failed_ttl if answer is None else self._ttl
                self._until = self._clock() + ttl
                self._asking = False
                self._changed.notify_all()


def client_network(address: str) -> str:
    """The start-limit key for a client: its IPv6 /64, or the address itself."""
    try:
        parsed = ip_address(address)
    except ValueError:
        return address
    if parsed.version == 6 and parsed.ipv4_mapped is None:
        return str(ip_network((parsed, 64), strict=False))
    return address


def _key(handle: str) -> str:
    return hashlib.sha256(handle.encode("utf-8", "backslashreplace")).hexdigest()
