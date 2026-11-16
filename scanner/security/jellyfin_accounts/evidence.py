"""The linked server's evidence for media decisions, cached within the 24-hour grace.

Contract for MU-20, MU-21, and MU-22: open `EvidenceCache.evidence(db, account,
resolve, now)` once per request or job run, pass its fields to
`media_access.service.decide_media`, and close it. The user is the snapshot the
last successful sync or sign-in stored. The catalog and library folders are
re-read when the cached copy is older than `refresh_after`; a failed re-read
falls back to the last complete copy while it is within the grace period, and a
replaced or disconnected server answers `server_mismatch`, which denies every
target. Item decisions fall back the same way, per item. A read publishes its
answer only if no read that began after it has published one, so a slow older
answer never replaces a newer one. A copy or decision timed after `now` is
neither reused nor a fallback, since its age is unknown after a clock step;
pass a `now` taken when asking. Nothing here persists: after a restart
during an outage, linked media stays `unresolved` until Jellyfin answers again.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from scanner.integrations.jellyfin_catalog import JellyfinCatalog
from scanner.integrations.jellyfin_library_folders import LibraryFolders
from scanner.integrations.jellyfin_user_snapshot import (
    ItemAccess,
    ItemAccessSnapshot,
    JellyfinUserSnapshot,
    Unverified,
    UnverifiedReason,
)
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security.access.identity import Account, JellyfinServerId, ProviderIdentity
from scanner.security.access.link_state import elapsed_within
from scanner.security.access.vocabulary import Role
from scanner.security.media_access.libraries import LibraryRoots
from scanner.security.media_access.matching import JellyfinCatalogIndex, ResolvePath

from . import store
from .sync import SYNC_INTERVAL, AdapterFactory, open_adapter

_REPLACED = Unverified(UnverifiedReason.SERVER_MISMATCH)

_reads = itertools.count()
"""Numbers each read as it begins; a higher number began later in this process."""
_publishing = threading.Lock()


@dataclass
class _Copy:
    value: JellyfinCatalog | LibraryFolders
    read_at: datetime
    read: int


@dataclass(frozen=True)
class _Decision:
    access: ItemAccess
    verified_at: datetime
    read: int


@dataclass
class CachedItems:
    """An item reader that answers from recent decisions when Jellyfin cannot."""

    adapter: JellyfinUserAdapter | None
    decisions: dict[tuple[ProviderIdentity, str], _Decision]
    now: datetime

    def item_access(
        self, identity: ProviderIdentity, item_ids: Sequence[str]
    ) -> ItemAccessSnapshot | Unverified:
        number = next(_reads)
        fresh = self.adapter.item_access(identity, item_ids) if self.adapter else _REPLACED
        if isinstance(fresh, ItemAccessSnapshot):
            with _publishing:
                for item_id, access in fresh.decisions.items():
                    known = self.decisions.get((identity, item_id))
                    if known is None or known.read < number:
                        self.decisions[(identity, item_id)] = _Decision(
                            access, fresh.verified_at, number
                        )
            return fresh
        if fresh.reason is UnverifiedReason.SERVER_MISMATCH:
            return fresh
        cached = [self.decisions.get((identity, item_id)) for item_id in item_ids]
        if any(
            entry is None or not elapsed_within(entry.verified_at, self.now) for entry in cached
        ):
            return fresh
        return ItemAccessSnapshot(
            identity,
            {item_id: entry.access for item_id, entry in zip(item_ids, cached, strict=True)},
            min(entry.verified_at for entry in cached),
        )


@dataclass
class MediaEvidence:
    """The keyword arguments `decide_media` needs for one account; close it after use."""

    user: JellyfinUserSnapshot | Unverified | None = None
    catalog: JellyfinCatalogIndex | Unverified | None = None
    libraries: LibraryRoots | Unverified | None = None
    items: CachedItems | None = None

    def close(self) -> None:
        if self.items is not None and self.items.adapter is not None:
            self.items.adapter.close()

    def __enter__(self) -> MediaEvidence:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@dataclass
class EvidenceCache:
    """Each linked server's last complete catalog, library folders, and item decisions."""

    adapters: AdapterFactory = open_adapter
    refresh_after: timedelta = SYNC_INTERVAL
    _copies: dict[tuple[str, JellyfinServerId], _Copy] = field(default_factory=dict)
    _items: dict[tuple[ProviderIdentity, str], _Decision] = field(default_factory=dict)

    def _read(
        self,
        kind: str,
        server: JellyfinServerId,
        read: Callable[[JellyfinServerId], JellyfinCatalog | LibraryFolders | Unverified],
        now: datetime,
    ):
        copy = self._copies.get((kind, server))
        if copy is not None and elapsed_within(copy.read_at, now, self.refresh_after):
            return copy.value
        number = next(_reads)
        fresh = read(server)
        if not isinstance(fresh, Unverified):
            with _publishing:
                known = self._copies.get((kind, server))
                if known is None or known.read < number:
                    self._copies[(kind, server)] = _Copy(fresh, fresh.read_at, number)
            return fresh
        if fresh.reason is UnverifiedReason.SERVER_MISMATCH or copy is None:
            return fresh
        return copy.value if elapsed_within(copy.read_at, now) else fresh

    def evidence(
        self, db: Session, account: Account, resolve: ResolvePath, now: datetime
    ) -> MediaEvidence:
        """The evidence for one account; the owner and local accounts need none."""

        if account.provider is None or account.role is Role.OWNER:
            return MediaEvidence()
        user = store.stored_snapshot(db, account.user_id)
        adapter = self.adapters(db)
        if adapter is None:
            return MediaEvidence(_REPLACED, _REPLACED, _REPLACED, CachedItems(None, {}, now))
        server = account.provider.server_id
        catalog = self._read("catalog", server, adapter.catalog, now)
        folders = self._read("folders", server, adapter.library_folders, now)
        return MediaEvidence(
            user if user is not None else Unverified(UnverifiedReason.INVALID_RESPONSE),
            JellyfinCatalogIndex(catalog, resolve)
            if isinstance(catalog, JellyfinCatalog)
            else catalog,
            LibraryRoots(folders, resolve) if isinstance(folders, LibraryFolders) else folders,
            CachedItems(adapter, self._items, now),
        )


evidence_cache = EvidenceCache()
