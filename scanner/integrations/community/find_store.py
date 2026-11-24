"""Server-held Find results, so a download takes its tier from the match, not the caller.

A `findKey` binds one account, service, local version and file generation to the
versions `/v1/match` returned, for 30 minutes (local API §2–§3). An unknown, expired
or foreign key is `find_expired`; a different file generation is `target_changed`.
"""

import secrets
import threading
import time
from dataclasses import dataclass

from .transport import CommunityFailure

FIND_TTL_SECONDS = 1800
MAX_FINDS = 256


@dataclass(frozen=True)
class FoundMatch:
    tier: str
    match_percent: int


@dataclass(frozen=True)
class FindRecord:
    owner_id: int
    service_url: str
    local_version_id: str
    guard: str
    matches: dict[str, FoundMatch]
    expires_at: float


class FindStore:
    """A bounded, expiring in-process map of `findKey` to its Find record."""

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.records: dict[str, FindRecord] = {}

    def put(
        self,
        *,
        owner_id: int,
        service_url: str,
        local_version_id: str,
        guard: str,
        matches: dict[str, FoundMatch],
    ) -> str:
        """Remember a Find response and return its opaque `findKey`."""
        with self.lock:
            now = self.clock()
            self.records = {k: v for k, v in self.records.items() if v.expires_at > now}
            while len(self.records) >= MAX_FINDS:
                self.records.pop(next(iter(self.records)))
            key = secrets.token_urlsafe(24)
            self.records[key] = FindRecord(
                owner_id, service_url, local_version_id, guard, matches, now + FIND_TTL_SECONDS
            )
            return key

    def get(
        self, key: str, *, owner_id: int, service_url: str, guard: str | None = None
    ) -> FindRecord:
        """The account's live record for `key`; `guard`, when given, must still match."""
        with self.lock:
            record = self.records.get(key) if isinstance(key, str) else None
            if record is not None and record.expires_at <= self.clock():
                self.records.pop(key, None)
                record = None
        if record is None or (record.owner_id, record.service_url) != (owner_id, service_url):
            raise CommunityFailure("find_expired")
        if guard is not None and guard != record.guard:
            raise CommunityFailure("target_changed")
        return record


finds = FindStore()
