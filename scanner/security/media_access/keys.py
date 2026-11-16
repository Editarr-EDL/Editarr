"""Stable keys for media items and the local sources that contain them.

A `MediaKey` names one item the way its source server does. It keys audit
records and decisions; it is never evidence of what the item is. A malformed
key raises `MalformedMediaKey`, so it can never reach a decision as anything but
a refusal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scanner.persistence.rows.db_policies import MediaSourceKind

_MAX_KEY = 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_GUID = re.compile(r"[0-9a-f]{32}")


class MalformedMediaKey(ValueError):
    """A key that does not name exactly one item of its source."""


@dataclass(frozen=True)
class SourceRef:
    """A local media source: a Jellyfin library, named by its canonical GUID."""

    kind: MediaSourceKind
    source_id: str

    def __post_init__(self) -> None:
        if not _GUID.fullmatch(self.source_id):
            raise MalformedMediaKey("A Jellyfin library ID must be a canonical GUID")


@dataclass(frozen=True)
class MediaKey:
    """One item: its source kind, the server GUID, and the item GUID."""

    kind: MediaSourceKind
    server: str
    item: str

    def __post_init__(self) -> None:
        for text in (self.server, self.item):
            if not text or len(text) > _MAX_KEY or _CONTROL.search(text):
                raise MalformedMediaKey("A media key part must be short printable text")
        if not (_GUID.fullmatch(self.server) and _GUID.fullmatch(self.item)):
            raise MalformedMediaKey(f"Not a {self.kind.value} item key")
