"""Server facts about an item and their correspondence to Jellyfin items.

Matching uses only what Jellyfin reports. It never reads a confirmed identity
or a scan association, which users may edit, so changing either cannot change
which Jellyfin items an item corresponds to.
Paths compare by spelling and by real path, so a symlinked spelling of a
Jellyfin item's file still corresponds to it. `libraries` places every item
against the library folders, so a file inside one must be a Jellyfin item's
file and inside libraries the user may open.
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from scanner.integrations.jellyfin_catalog import JellyfinCatalog
from scanner.integrations.jellyfin_user_snapshot import InvalidPayload, canonical_guid
from scanner.models import JellyfinMediaItem
from scanner.persistence.rows.db_policies import MediaSourceKind
from scanner.server_paths import UnresolvablePath

from .keys import MediaKey, SourceRef

ResolvePath = Callable[[str], str]
"""Map a server-reported path to the resolved local path, as path mappings do.

Raises `UnresolvablePath` for a server path this host cannot resolve to a file."""


def spellings(path: str) -> frozenset[str]:
    """A local path as given and as its real path in the host's case rules."""

    try:
        return frozenset({path, os.path.normcase(os.path.realpath(path))})
    except (OSError, ValueError):
        return frozenset({path})


@dataclass(frozen=True)
class CatalogEntry:
    """One Jellyfin item as the server key sees it, with every spelling of its paths."""

    item_id: str
    library_id: str | None
    paths: frozenset[str]


@dataclass(frozen=True)
class Matched:
    """The Jellyfin items an item corresponds to; every one must allow it."""

    entries: tuple[CatalogEntry, ...]


@dataclass(frozen=True)
class Restricted:
    """A path lies inside a library the user's Jellyfin policy does not open."""


@dataclass(frozen=True)
class Unmatched:
    """No Jellyfin item in a complete listing corresponds and every path lies outside
    every Jellyfin library folder. Only an owner exception can allow it."""


@dataclass(frozen=True)
class InLibrary:
    """A path lies inside a Jellyfin library folder, yet no Jellyfin item has that file.
    No exception applies."""


@dataclass(frozen=True)
class Unresolved:
    """Correspondence could not be established, for example while Jellyfin is unreachable
    or a path's place against the library folders cannot be proven."""


Correspondence = Matched | Restricted | Unmatched | InLibrary | Unresolved


@dataclass(frozen=True)
class MediaTarget:
    """One item to decide, built from its source server's own record.

    `jellyfin_item` is set only for an item of the configured Jellyfin server,
    which corresponds to itself. Other items match by `paths`, the resolved
    local paths.
    """

    key: MediaKey
    source: SourceRef | None
    """None when the item's library is unknown, which no local scope can grant."""
    paths: frozenset[str] = frozenset()
    jellyfin_item: CatalogEntry | None = None


class JellyfinCatalogIndex:
    """Jellyfin items by path spelling, from one complete listing.

    Holding an index proves which server was listed and when, so a decision can
    refuse another server's listing or a stale one.
    """

    def __init__(self, catalog: JellyfinCatalog, resolve: ResolvePath):
        self.server_id = catalog.server_id
        self.read_at = catalog.read_at
        by_path: dict[str, set[CatalogEntry]] = defaultdict(set)
        for item in catalog.items:
            entry = catalog_entry(item, resolve)
            if entry is None:
                continue
            for path in entry.paths:
                by_path[path].add(entry)
        self._by_path: Mapping[str, set[CatalogEntry]] = MappingProxyType(dict(by_path))

    def match(self, target: MediaTarget) -> tuple[CatalogEntry, ...]:
        """The Jellyfin items whose files are the target's files, by item ID."""

        found = frozenset().union(
            *(self._by_path.get(key, ()) for path in target.paths for key in spellings(path))
        )
        return tuple(sorted(found, key=lambda entry: entry.item_id))


def _guid(value: str | None) -> str | None:
    try:
        return canonical_guid(value)
    except InvalidPayload:
        return None


def _host_spellings(path: str, resolve: ResolvePath) -> frozenset[str] | None:
    try:
        return spellings(resolve(path))
    except UnresolvablePath:
        return None


def _entry(item: JellyfinMediaItem, resolve: ResolvePath) -> tuple[CatalogEntry, bool] | None:
    """The item's entry and whether this host can resolve every file its versions report."""

    item_id = _guid(item.id)
    if item_id is None:
        return None
    paths = {item.path, *(version.path for version in item.versions)}
    found = [_host_spellings(path, resolve) for path in paths if path]
    entry = CatalogEntry(
        item_id=item_id,
        library_id=_guid(item.library_id),
        paths=frozenset().union(*(named for named in found if named is not None)),
    )
    return entry, None not in found


def catalog_entry(item: JellyfinMediaItem, resolve: ResolvePath) -> CatalogEntry | None:
    """Index one server-key Jellyfin item with every path its versions report.

    A file this host cannot resolve adds no path, so that file proves nothing
    about where it lies.
    """

    found = _entry(item, resolve)
    return found[0] if found else None


def jellyfin_target(server_id: str, item: JellyfinMediaItem, resolve: ResolvePath) -> MediaTarget:
    """A configured-server Jellyfin item; raises `MalformedMediaKey` for a non-GUID item ID.

    An item with a file this host cannot resolve has no library, so no scope reaches it.
    """

    found = _entry(item, resolve)
    if found is None or not found[1]:
        item_id = found[0].item_id if found else item.id
        return MediaTarget(MediaKey(MediaSourceKind.JELLYFIN_LIBRARY, server_id, item_id), None)
    entry = found[0]
    return MediaTarget(
        key=MediaKey(MediaSourceKind.JELLYFIN_LIBRARY, server_id, entry.item_id),
        source=(
            SourceRef(MediaSourceKind.JELLYFIN_LIBRARY, entry.library_id)
            if entry.library_id
            else None
        ),
        jellyfin_item=entry,
    )
