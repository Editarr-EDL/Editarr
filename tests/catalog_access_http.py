"""MU-20 HTTP harness: the real catalog routers behind the access middleware.

Accounts come from `live_accounts` (`mu04-access-builders-3`): owner 1, admins
2 and 3, user 4 linked to the Jellyfin user Casey, and user 5, a local account
whose scope is the Family library. Media is MU-11's `mu11-media-access-4`,
with real synthetic files under its temporary root and a path mapping for the
Jellyfin spelling.

Only established boundaries are replaced, and each records its calls: the
Jellyfin catalog client (`FakeJellyfin`) and
MU-16's evidence adapter (`EvidenceAdapter`, the scripted stand-in its own
tests use). Every time is the fixed `NOW`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

import scanner.api as root
from scanner.db import UserProviderIdentityRow
from scanner.http import (
    audio_tracks_api,
    catalog_access,
    catalog_candidates_api,
    catalog_library_api,
    catalog_targets,
    scan_catalog_api,
)
from scanner.integrations.jellyfin import JellyfinNotFound
from scanner.integrations.jellyfin_catalog import parse_catalog
from scanner.integrations.jellyfin_library_folders import parse_library_folders
from scanner.integrations.jellyfin_user_snapshot import ItemAccess, ItemAccessSnapshot
from scanner.models import (
    JellyfinLibrary,
    JellyfinMediaItem,
    JellyfinSettingsUpdate,
    PathMappingCreate,
)
from scanner.persistence.repository import media_access as scopes
from scanner.repository import create_path_mapping, update_jellyfin_settings
from scanner.security.access import link_state
from scanner.security.jellyfin_accounts import reads, store
from scanner.security.jellyfin_accounts.evidence import EvidenceCache
from tests.jellyfin_user_boundary import IDS, NOW, PRIMARY
from tests.media_access_support import (
    EPISODE,
    FAMILY,
    FAMILY_FILM,
    FEATURE,
    JELLYFIN_ITEMS,
    LATE_SHOW,
    MATURE,
    MEDIA,
    MOVIES,
    SERIES,
    VIRTUAL_FOLDERS,
    jellyfin_user,
    listing,
    record,
)
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import USER, LiveAccounts

FIXTURE_VERSION = "mu20-catalog-1"
LOCAL = USER + 1
RATINGS = {FEATURE: "PG", FAMILY_FILM: "G", LATE_SHOW: "PG", MATURE: "R", SERIES: "TV-PG"}
LIBRARIES = (
    JellyfinLibrary(id=MOVIES, name="Movies", locations=["/jellyfin/movies"]),
    JellyfinLibrary(id=FAMILY, name="Family", locations=["/jellyfin/family"]),
)
ALLOWED = {FAMILY_FILM, LATE_SHOW, SERIES, EPISODE}
"""What Jellyfin opens for Casey: the Family library, under her parental limit."""


def _jellyfin(item_id: str, library: str | None) -> JellyfinMediaItem:
    item = JELLYFIN_ITEMS[item_id]
    tmdb = next((alias.value for alias in item.media_identity.identifiers), None)
    return item.model_copy(
        update={
            "library_id": library,
            "official_rating": RATINGS.get(item_id),
            "duration_seconds": 100.0,
            "media_provider_id": f"tmdb:{tmdb}" if item.item_type == "Movie" else None,
        }
    )


@dataclass
class FakeJellyfin:
    """The Jellyfin catalog client: libraries, listings, details, children, and artwork."""

    calls: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, *_credentials):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def libraries(self):
        self.calls.append(("libraries", ""))
        return list(LIBRARIES)

    def all_items(self, library_id, recursive=False, include_item_types=(), search_term=""):
        self.calls.append(("search" if search_term else "listing", library_id))
        return [
            _jellyfin(item_id, library_id)
            for item_id, item in JELLYFIN_ITEMS.items()
            if item.library_id == library_id
            and item.item_type in include_item_types
            and item.item_type != "Series"
            and (not search_term or search_term.casefold() in item.name.casefold())
        ]

    def item(self, item_id):
        self.calls.append(("item", item_id))
        try:
            return _jellyfin(item_id, "")
        except KeyError as exc:
            raise JellyfinNotFound("Item not found") from exc

    def children(self, item_id):
        self.calls.append(("children", item_id))
        return [_jellyfin(EPISODE, item_id)] if item_id == SERIES else []

    def seasons(self, item_id):
        self.calls.append(("seasons", item_id))
        return []

    def primary_image(self, item_id, _tag=None):
        self.calls.append(("image", item_id))
        return f"artwork {item_id}".encode(), "image/png"


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


@dataclass
class EvidenceAdapter:
    """MU-08's adapter as MU-16's tests script it: Casey opens `allowed` only."""

    clock: Clock
    allowed: set[str] = field(default_factory=lambda: set(ALLOWED))
    reads: list[str] = field(default_factory=list)

    def catalog(self, server):
        self.reads.append("catalog")
        records = [record(item) for item in JELLYFIN_ITEMS.values()]
        return parse_catalog([listing(records)], server, self.clock())

    def library_folders(self, server):
        self.reads.append("folders")
        return parse_library_folders(VIRTUAL_FOLDERS, server, self.clock())

    def item_access(self, identity, item_ids):
        self.reads.extend(f"item:{item_id}" for item_id in item_ids)
        decisions = {
            item_id: ItemAccess.ALLOWED if item_id in self.allowed else ItemAccess.RESTRICTED
            for item_id in item_ids
        }
        return ItemAccessSnapshot(identity, decisions, self.clock())

    def close(self) -> None:
        return None


@dataclass
class CatalogApp:
    client: object
    accounts: LiveAccounts
    clock: Clock
    jellyfin: FakeJellyfin
    evidence: EvidenceAdapter

    def get(self, user_id: int, path: str, **params):
        self.client.cookies.clear()
        return self.client.get(path, headers=self.accounts.cookie(user_id), params=params)

    def post(self, user_id: int, path: str, body: dict):
        self.client.cookies.clear()
        return self.client.post(path, headers=self.accounts.unsafe_cookie(user_id), json=body)

    def set_scope(self, user_id: int, *sources: tuple[str, str]) -> None:
        with self.accounts.factory() as db:
            scopes.replace_scope(
                db, user_id, all_sources=False, sources=frozenset(sources), now=self.clock()
            )
            db.commit()

    def sync_casey(self, **policy) -> None:
        """Store a newer verified snapshot of Casey, as a sync a minute later would."""

        self.clock.now += timedelta(minutes=1)
        snapshot = jellyfin_user("Casey", verified_at=self.clock())
        snapshot = replace(snapshot, media=replace(snapshot.media, **policy))
        with self.accounts.factory() as db:
            store.apply(db, USER, snapshot, reads.begin_read(db, self.clock))


def _clear_caches() -> None:
    with root.LIBRARY_ITEMS_CACHE_LOCK:
        root.LIBRARY_ITEMS_CACHE.clear()
        root.JELLYFIN_ITEM_CACHE.clear()
    catalog_targets.jellyfin_libraries.clear()


def serve_catalog(monkeypatch, accounts: LiveAccounts, tmp_path: Path) -> CatalogApp:
    """Start the harness; the caller closes `app.client`."""

    clock = Clock()
    monkeypatch.setattr(link_state, "link_clock", clock)
    _clear_caches()
    jellyfin, evidence = FakeJellyfin(), EvidenceAdapter(clock)
    monkeypatch.setattr(root, "JellyfinClient", jellyfin)
    monkeypatch.setattr(
        catalog_access, "evidence_cache", EvidenceCache(adapters=lambda db: evidence)
    )
    client = serve_access_fixture(monkeypatch, accounts)
    for router in (
        catalog_library_api.router,
        catalog_candidates_api.router,
        scan_catalog_api.router,
        audio_tracks_api.router,
    ):
        client.app.include_router(router)
    with accounts.factory() as db:
        update_jellyfin_settings(
            db, JellyfinSettingsUpdate(server_url="http://jellyfin.test", api_key="jellyfin-key")
        )
        create_path_mapping(db, PathMappingCreate(remote_path="/jellyfin", local_path=str(MEDIA)))
        db.add(
            UserProviderIdentityRow(
                user_id=USER,
                server_id=PRIMARY,
                provider_user_id=IDS["Casey"],
                link_state="verified",
            )
        )
        db.commit()
        store.apply(db, USER, jellyfin_user("Casey"), reads.begin_read(db, lambda: NOW))
    app = CatalogApp(client, accounts, clock, jellyfin, evidence)
    app.set_scope(LOCAL, ("jellyfin_library", FAMILY))
    return app
