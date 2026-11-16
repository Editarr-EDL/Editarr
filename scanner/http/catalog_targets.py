"""Media targets for catalog records, built only from what a source server reported.

Jellyfin's item routes do not report an item's library, so a local account's
Jellyfin target is placed by the item's server path against the libraries'
reported locations, both in Jellyfin's own path namespace. A record listed
under a library or below an authorized parent takes that library instead.
Client-supplied names, paths, and IDs never build a target; they only select
among records the server returned.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Generic, TypeVar

from scanner import api as root
from scanner.integrations.jellyfin import JellyfinError
from scanner.models import JellyfinLibrary, JellyfinMediaItem
from scanner.security.media_access.keys import MalformedMediaKey
from scanner.security.media_access.matching import (
    MediaTarget,
    ResolvePath,
    jellyfin_target,
)

T = TypeVar("T")

LISTING_SECONDS = 300.0
"""How long a server-key library listing is reused, like MU-16's catalog copy."""


class SourceUnavailable(Exception):
    """The source server could not answer; the message is safe to show."""


class Listings(Generic[T]):
    """Short-lived unfiltered server-key reads, shared by every caller and never filtered."""

    def __init__(self, seconds: float = LISTING_SECONDS) -> None:
        self.seconds = seconds
        self._entries: dict[Hashable, tuple[float, T]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable, read: Callable[[], T], *, refresh: bool = False) -> T:
        with self._lock:
            entry = self._entries.get(key)
        if entry is not None and not refresh and time.monotonic() - entry[0] < self.seconds:
            return entry[1]
        value = read()
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


jellyfin_libraries: Listings[tuple[JellyfinLibrary, ...]] = Listings()


def connection_key(server_url: str) -> str:
    """A stable 32-hex key for the configured Jellyfin connection.

    Only linked decisions compare a Jellyfin key's server, and they use the
    linked identity's server ID; owner and local decisions never read it.
    """

    return hashlib.sha256(server_url.encode()).hexdigest()[:32]


def libraries(credentials: tuple[str, str]) -> tuple[JellyfinLibrary, ...]:
    """The configured server's libraries and their reported locations."""

    def read() -> tuple[JellyfinLibrary, ...]:
        try:
            with root.JellyfinClient(*credentials) as client:
                return tuple(client.libraries())
        except JellyfinError as error:
            raise SourceUnavailable(str(error)) from error

    return jellyfin_libraries.get(credentials, read)


def jellyfin_item_target(
    server: str, item: JellyfinMediaItem, library: str | None, resolve: ResolvePath
) -> MediaTarget | None:
    """A Jellyfin target in `library`, or None when the item ID is not a GUID."""

    try:
        return jellyfin_target(server, item.model_copy(update={"library_id": library}), resolve)
    except MalformedMediaKey:
        return None


@dataclass(frozen=True)
class StoredMedia:
    """A `media_items` row's source keys and the scan's resolved file, as stored."""

    server_id: str
    item_id: str
    resolved_path: str | None


def stored_media_target(
    stored: StoredMedia,
    *,
    jellyfin: Callable[[str], MediaTarget | None],
) -> MediaTarget | None:
    """Rebuild a stored media item's target from its Jellyfin server's current record."""

    return jellyfin(stored.item_id)
