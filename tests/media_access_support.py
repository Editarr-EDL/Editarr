"""Synthetic media for MU-11 decisions, fixture version `mu11-media-access-4`.

The Jellyfin side reuses the four items of `editarr-jellyfin-users-1`, adding the
paths, provider IDs, and media types a server key would report, complete `/Items`
listings of them, the users' policies, and two library folders, Movies and Family.
Casey may open only the family film: the feature is outside
Casey's libraries, the late show carries a blocked tag, and the mature film
exceeds Casey's rating. Path-only targets reach those Jellyfin items by path,
sit unindexed inside a library folder, or sit outside every folder. Every file
is an empty synthetic file under a private temporary root that is removed at
exit; every ID and number is invented.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

from scanner.integrations.jellyfin_catalog import parse_catalog
from scanner.integrations.jellyfin_library_folders import parse_library_folders
from scanner.integrations.jellyfin_user_snapshot import JellyfinUserSnapshot, parse_user
from scanner.modeling.media_identity import EpisodeNumber, MediaIdentity, ProviderIdentifier
from scanner.models import JellyfinMediaItem, JellyfinMediaVersion
from scanner.persistence.rows.db_policies import MediaSourceKind
from scanner.security.access.identity import JellyfinServerId
from scanner.security.media_access.keys import MediaKey
from scanner.security.media_access.libraries import LibraryRoots
from scanner.security.media_access.matching import JellyfinCatalogIndex, MediaTarget
from tests.jellyfin_user_boundary import FIXTURE, NOW, PRIMARY, user_dto

FIXTURE_VERSION = "mu11-media-access-4"
ROOT = Path(tempfile.mkdtemp(prefix="mu11-media-")).resolve()
atexit.register(shutil.rmtree, ROOT, ignore_errors=True)
MEDIA = ROOT / "media"
MOVIES = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
FAMILY = "b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2"
FEATURE = "c3000000000000000000000000000001"
FAMILY_FILM = "c3000000000000000000000000000002"
LATE_SHOW = "c3000000000000000000000000000003"
MATURE = "c3000000000000000000000000000004"
SERIES = "e5000000000000000000000000000001"
EPISODE = "e5000000000000000000000000000002"


def resolve(path: str) -> str:
    """The path mapping: Jellyfin mounts the media under `/jellyfin/`."""

    if path.startswith("/jellyfin/"):
        return f"{MEDIA}/{path.removeprefix('/jellyfin/')}"
    return path


VIRTUAL_FOLDERS = [
    {"Name": "Movies", "ItemId": MOVIES, "Locations": ["/jellyfin/movies"]},
    {"Name": "Family", "ItemId": FAMILY, "Locations": ["/jellyfin/family"]},
]


def libraries(server: JellyfinServerId = PRIMARY, read_at: datetime = NOW) -> LibraryRoots:
    """A complete library-folder read of `server`, mapped like the Jellyfin paths."""

    return LibraryRoots(parse_library_folders(VIRTUAL_FOLDERS, server, read_at), resolve)


def movie(tmdb: str, source: str) -> MediaIdentity:
    return MediaIdentity("movie", (ProviderIdentifier("tmdb", "movie", tmdb, source),))


def episode(series_tvdb: str, season: int, number: int, source: str) -> MediaIdentity:
    return MediaIdentity(
        "episode",
        (),
        series_identifiers=(ProviderIdentifier("tvdb", "series", series_tvdb, source),),
        episode_numbers=(EpisodeNumber("aired", season, number),),
    )


def _jellyfin(item_id, library, kind, name, path, identity, versions=()) -> JellyfinMediaItem:
    return JellyfinMediaItem(
        id=item_id,
        library_id=library,
        item_type=kind,
        name=name,
        path=path,
        media_identity=identity,
        versions=[JellyfinMediaVersion(id=f"v{index}", path=p) for index, p in enumerate(versions)],
    )


JELLYFIN_ITEMS = {
    FEATURE: _jellyfin(
        FEATURE,
        MOVIES,
        "Movie",
        "Synthetic Feature",
        "/jellyfin/movies/Synthetic Feature/Synthetic Feature - 1080p.mkv",
        movie("9001", "jellyfin.movie"),
        versions=(
            "/jellyfin/movies/Synthetic Feature/Synthetic Feature - 1080p.mkv",
            "/jellyfin/movies/Synthetic Feature/Synthetic Feature - 2160p.mkv",
        ),
    ),
    FAMILY_FILM: _jellyfin(
        FAMILY_FILM,
        FAMILY,
        "Movie",
        "Synthetic Family Film",
        "/jellyfin/family/Synthetic Family Film.mkv",
        movie("9002", "jellyfin.movie"),
    ),
    LATE_SHOW: _jellyfin(
        LATE_SHOW,
        FAMILY,
        "Movie",
        "Synthetic Late Show",
        "/jellyfin/family/Synthetic Late Show.mkv",
        movie("9003", "jellyfin.movie"),
    ),
    MATURE: _jellyfin(
        MATURE,
        FAMILY,
        "Movie",
        "Synthetic Mature Film",
        "/jellyfin/family/Synthetic Mature Film.mkv",
        movie("9004", "jellyfin.movie"),
    ),
    SERIES: _jellyfin(
        SERIES,
        FAMILY,
        "Series",
        "Synthetic Series",
        "/jellyfin/family/Synthetic Series",
        MediaIdentity("series", (ProviderIdentifier("tvdb", "series", "7001", "jellyfin.series"),)),
    ),
    EPISODE: _jellyfin(
        EPISODE,
        FAMILY,
        "Episode",
        "Synthetic Episode",
        "/jellyfin/family/Synthetic Series/S01E02.mkv",
        episode("7001", 1, 2, "jellyfin.series"),
    ),
}


def record(item: JellyfinMediaItem, **changes: object) -> dict[str, object]:
    """The `BaseItemDto` a server-key `/Items` listing reports for a fixture item."""

    identity = item.media_identity
    found: dict[str, object] = {
        "Id": item.id,
        "Name": item.name,
        "Type": item.item_type,
        "MediaType": "Unknown" if item.item_type == "Series" else "Video",
        "Path": item.path,
        "ProviderIds": {alias.provider.title(): alias.value for alias in identity.identifiers},
        "MediaSources": [{"Id": version.id, "Path": version.path} for version in item.versions],
    }
    if identity.entity_type == "episode":
        number = identity.episode_numbers[0]
        found |= {"SeriesId": SERIES, "ParentIndexNumber": number.season}
        found["IndexNumber"] = number.number
    return found | changes


def listing(records: list[dict[str, object]], page: int = 500) -> list[dict[str, object]]:
    """The pages of one complete listing of `records`."""

    starts = range(0, max(len(records), 1), page)
    return [
        {
            "Items": records[start : start + page],
            "TotalRecordCount": len(records),
            "StartIndex": start,
        }
        for start in starts
    ]


def jellyfin_items(
    records: list[dict[str, object]],
    boxsets: dict[str, tuple[dict[str, object], list[str]]] | None = None,
    parts: dict[str, list[dict[str, object]]] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Answer server-key reads as Jellyfin 12.0.0 does: keep only `/Items` records of
    the requested `IncludeItemTypes` and `MediaTypes`, replace each collection's
    members with its `BoxSet` unless `CollapseBoxSetItems=false` (unless only other
    types are requested), then serve the requested page. `boxsets` maps a
    collection ID to its record and member IDs; `parts` maps a stacked item's ID to
    the records `/Videos/{id}/AdditionalParts` returns."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/AdditionalParts"):
            found = (parts or {}).get(request.url.path.split("/")[2], [])
            return httpx.Response(
                200, json={"Items": found, "TotalRecordCount": len(found), "StartIndex": 0}
            )
        assert request.url.path == "/Items"
        query = request.url.params
        kept = [
            found
            for found in records
            if all(
                name not in query or found.get(field) in query[name].split(",")
                for name, field in (("IncludeItemTypes", "Type"), ("MediaTypes", "MediaType"))
            )
        ]
        types = set(query.get("IncludeItemTypes", "Movie").split(","))
        if query.get("CollapseBoxSetItems") != "false" and types & {"Movie", "Series"}:
            for boxset, members in (boxsets or {}).values():
                at = next((i for i, found in enumerate(kept) if found["Id"] in members), None)
                if at is not None:
                    kept = [found for found in kept if found["Id"] not in members]
                    kept.insert(at, boxset)
        start, size = int(query["StartIndex"]), int(query["Limit"])
        page = {"Items": kept[start : start + size], "TotalRecordCount": len(kept)}
        return httpx.Response(200, json={**page, "StartIndex": start})

    return answer


def catalog(
    records: list[dict[str, object]] | None = None,
    server: JellyfinServerId = PRIMARY,
    read_at: datetime = NOW,
) -> JellyfinCatalogIndex:
    """An index of one complete listing, by default of every fixture item."""

    records = [record(item) for item in JELLYFIN_ITEMS.values()] if records is None else records
    return JellyfinCatalogIndex(parse_catalog([listing(records)], server, read_at), resolve)


def jellyfin_user(
    name: str = "Casey", server: JellyfinServerId = PRIMARY, verified_at: datetime = NOW
) -> JellyfinUserSnapshot:
    """A baseline user's verified snapshot. Casey's policy opens only the Family library."""

    found = next(user for user in FIXTURE["states"]["baseline"]["users"] if user["Name"] == name)
    template = user_dto(name, server)
    policy = {**template["Policy"], **found.get("Policy", {})}
    return parse_user({**template, **found, "Policy": policy}, server, verified_at)


FILES = (
    "movies/Synthetic Feature/Synthetic Feature - 1080p.mkv",
    "movies/Synthetic Feature/Synthetic Feature - 2160p.mkv",
    "family/Synthetic Family Film.mkv",
    "family/Synthetic Late Show.mkv",
    "family/Synthetic Mature Film.mkv",
    "family/Synthetic Series/S01E02.mkv",
    "family/Synthetic Camcorder.mkv",
    "../srv/other/Synthetic Home Video.mkv",
)
for _name in FILES:
    (MEDIA / _name).parent.mkdir(parents=True, exist_ok=True)
    (MEDIA / _name).touch()


def path_target(resolved_path: str, item: str = "f7000000000000000000000000000001") -> MediaTarget:
    """An item known only by its resolved local path, so only path correspondence can
    place it: no library and no Jellyfin record of its own."""

    return MediaTarget(
        key=MediaKey(MediaSourceKind.JELLYFIN_LIBRARY, str(PRIMARY), item),
        source=None,
        paths=frozenset({resolved_path}),
    )


TARGETS = {
    "path-family-film": path_target(
        f"{MEDIA}/family/Synthetic Family Film.mkv", "f7000000000000000000000000000001"
    ),
    "path-late-show": path_target(
        f"{MEDIA}/family/Synthetic Late Show.mkv", "f7000000000000000000000000000002"
    ),
    "path-alternate-version": path_target(
        f"{MEDIA}/movies/Synthetic Feature/Synthetic Feature - 2160p.mkv",
        "f7000000000000000000000000000003",
    ),
    "path-in-library": path_target(
        f"{MEDIA}/family/Synthetic Camcorder.mkv", "f7000000000000000000000000000004"
    ),
    "path-unmatched": path_target(
        f"{ROOT}/srv/other/Synthetic Home Video.mkv", "f7000000000000000000000000000005"
    ),
}
OUTSIDE = frozenset({"path-unmatched"})
"""Targets outside every library folder that match no Jellyfin item."""
IN_LIBRARY = frozenset({"path-in-library"})
"""Targets inside a library folder that match no Jellyfin item."""
MOVIES_LIBRARY = frozenset({"path-alternate-version"})
"""Targets inside the Movies library, which Casey's Jellyfin policy does not open."""
