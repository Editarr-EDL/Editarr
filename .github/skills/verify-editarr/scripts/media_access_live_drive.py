"""The synthetic Jellyfin library and Casey's checks that media_access_live.py drives.

Movies, Family, Shows, Home, and Songs libraries hold one-second clips, a song,
and a photo, with a local trailer, extras, a theme video, a special, a stacked
movie, and a movie merged into another. Film A, Film B, and Show X belong to
collections. A trailer and a second part in Movies and a song in Songs are
symlinks to files in Elsewhere, outside every library. Casey may open only Family
and Home. Jellyfin's Collections library
folder under /config is left unmapped, as on a host that mounts only media.
"""

from __future__ import annotations

import shutil
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from scanner.formats.export_layout import parse_jellyfin_user_id
from scanner.integrations import jellyfin_catalog
from scanner.integrations.jellyfin_user_snapshot import (
    JellyfinUserSnapshot,
    Unverified,
    UnverifiedReason,
    canonical_guid,
)
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security.access.identity import ProviderIdentity, ProviderLinkState
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDenial, MediaDenied
from scanner.security.media_access.keys import MediaKey, MediaSourceKind
from scanner.security.media_access.libraries import LibraryRoots
from scanner.security.media_access.matching import (
    JellyfinCatalogIndex,
    MediaTarget,
    jellyfin_target,
)
from scanner.security.media_access.resolver import LinkedSubject
from scanner.security.media_access.service import decide_media

FILM_A = "Movies/Film A (2001) [tmdbid-9001]"
FILM_B = "Movies/Film B (2002) [tmdbid-9002]"
FILM_C = "Family/Film C (2003) [tmdbid-9003]"
FILM_E = "Movies/Film E (2005) [tmdbid-9005]/Film E (2005) [tmdbid-9005].mkv"
FILM_F = "Movies/Film F (2006) [tmdbid-9006]/Film F (2006) [tmdbid-9006].mkv"
FILM_G = "Movies/Film G (2007) [tmdbid-9007]/Film G (2007) [tmdbid-9007]"
SHOW = "Shows/Show X (2010) [tvdbid-7001]"
LAYOUT_FILES = {
    "film-a": f"{FILM_A}/Film A (2001) [tmdbid-9001].mkv",
    "film-b-2160p": f"{FILM_B}/Film B (2002) [tmdbid-9002] - 2160p.mkv",
    "film-c": f"{FILM_C}/Film C (2003) [tmdbid-9003].mkv",
}
LAYOUT = {
    LAYOUT_FILES["film-a"]: "mkv",
    f"{FILM_B}/Film B (2002) [tmdbid-9002] - 1080p.mkv": "mkv",
    LAYOUT_FILES["film-b-2160p"]: "mkv",
    f"{FILM_A}/trailers/Teaser.mkv": "mkv",
    f"{FILM_A}/behind the scenes/Making Of.mkv": "mkv",
    f"{FILM_A}/backdrops/Theme.mkv": "mkv",
    f"{FILM_A}/featurettes/Featurette.mkv": "mkv",
    FILM_E: "mkv",
    FILM_F: "mkv",
    f"{FILM_G} - cd1.mkv": "mkv",
    LAYOUT_FILES["film-c"]: "mkv",
    f"{FILM_C}/trailers/Family Trailer.mkv": "mkv",
    f"{SHOW}/Season 00/Show X S00E01.mkv": "mkv",
    f"{SHOW}/Season 01/Show X S01E01.mkv": "mkv",
    f"{SHOW}/Season 01/Show X S01E02.mkv": "mkv",
    f"{SHOW}/trailers/Show Trailer.mkv": "mkv",
    "Home/Clip.mkv": "mkv",
    "Home/Photo.jpg": "jpg",
    "Songs/Song.m4a": "m4a",
    "Elsewhere/Home.mkv": "mkv",
    "Elsewhere/Shared Trailer.mkv": "mkv",
    "Elsewhere/Shared Part.mkv": "mkv",
    "Elsewhere/Shared Song.m4a": "m4a",
}
LINKS = {
    f"{FILM_A}/trailers/Shared Trailer.mkv": "../../../Elsewhere/Shared Trailer.mkv",
    f"{FILM_G} - cd2.mkv": "../../Elsewhere/Shared Part.mkv",
    "Songs/Shared Song.m4a": "../Elsewhere/Shared Song.m4a",
}
COLLECTIONS = {"Synthetic Films": ("Film A", "Film B"), "Synthetic Shows": ("Show X",)}
LIBRARIES = (
    ("Movies", "movies", ["Movie"]),
    ("Family", "movies", ["Movie"]),
    ("Shows", "tvshows", ["Series", "Season", "Episode"]),
    ("Home", "homevideos", ["Video"]),
    ("Songs", "music", []),
)
UNFILTERED = {
    "Recursive": "true",
    "Fields": "Path,ProviderIds,MediaSources,SeriesId,ParentIndexNumber",
    "EnableImages": "false",
    "EnableUserData": "false",
}
GROUPED = {
    name: {**listing, "CollapseBoxSetItems": "true", "StartIndex": "0", "Limit": "500"}
    for name, listing in (
        ("grouped_files", jellyfin_catalog.FILE_LISTING),
        ("grouped_series", jellyfin_catalog.SERIES_LISTING),
    )
}
"""The catalog listings with Jellyfin's collection grouping on, as a control."""
VERIFIED = MediaAllowed(MediaBasis.JELLYFIN_VERIFIED)
CLOSED = MediaDenied(MediaDenial.NOT_IN_SCOPE)


def check(actual: object, wanted: object) -> dict[str, object]:
    return {"actual": actual, "wanted": wanted, "ok": actual == wanted}


def library_files() -> list[str]:
    """Every synthetic file inside a library folder, symlinks included."""

    return [path for path in LAYOUT if not path.startswith("Elsewhere/")] + list(LINKS)


def write_media(media: Path) -> None:
    for path, kind in LAYOUT.items():
        (media / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(media / f"plain.{kind}", media / path)
    for path, target in LINKS.items():
        (media / path).symlink_to(target)


def group(keyed, admin) -> dict[str, object]:
    """Merge Film E into Film F and collect films and a show until Jellyfin groups them.

    Jellyfin shows a collection's grouping only after a delay, sometimes only once
    its members are added again, so the members are re-added every 15 seconds.
    """

    query = {**UNFILTERED, "IncludeItemTypes": "Movie,Series"}
    named = {
        i["Name"].split(" (")[0]: i["Id"]
        for i in keyed.call("GET", "/Items", params=query).json()["Items"]
    }
    admin.call(
        "POST", "/Videos/MergeVersions", params={"ids": f"{named['Film E']},{named['Film F']}"}
    )
    members = {name: ",".join(named[m] for m in films) for name, films in COLLECTIONS.items()}
    made = {
        name: admin.call("POST", "/Collections", params={"name": name, "ids": ids}).json()["Id"]
        for name, ids in members.items()
    }
    deadline, added = time.monotonic() + 180, time.monotonic()
    while True:
        types = [
            {i["Type"] for i in keyed.call("GET", "/Items", params=page).json()["Items"]}
            for page in (GROUPED["grouped_files"], GROUPED["grouped_series"])
        ]
        visible = all("BoxSet" in found for found in types)
        if visible or time.monotonic() > deadline:
            return {"jellyfin_groups_the_collections": check(visible, True)}
        if time.monotonic() - added > 15:
            for name, ids in members.items():
                admin.call("POST", f"/Collections/{made[name]}/Items", params={"ids": ids})
            added = time.monotonic()
        time.sleep(3)


def listings(keyed) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Read the unfiltered and grouped listings whole, each catalog listing whole and in
    pages of three, and the later parts of each stacked item."""

    entries: list[dict[str, object]] = []

    def get(path: str, query: dict[str, str]) -> dict[str, object]:
        body = keyed.call("GET", path, params=query).json()
        entries.append({"path": path, "query": query, "status": 200, "body": body})
        return body

    get("/System/Info/Public", {})
    folders = get("/Library/VirtualFolders", {})
    results: dict[str, object] = {
        "collections_library_is_boxsets": check(
            [(f["Name"], f["Locations"]) for f in folders if f.get("CollectionType") == "boxsets"],
            [("Collections", ["/config/data/collections"])],
        )
    }
    for name, listing in (
        ("unfiltered", UNFILTERED),
        *GROUPED.items(),
        ("files", jellyfin_catalog.FILE_LISTING),
        ("series", jellyfin_catalog.SERIES_LISTING),
    ):
        whole = get("/Items", {**listing, "StartIndex": "0", "Limit": "500"})
        total, items = whole["TotalRecordCount"], whole["Items"]
        results[f"{name}_listing"] = {
            "total": total,
            "delivered": len(items),
            "types": sorted({f"{i.get('Type')}/{i.get('ExtraType')}" for i in items}),
        }
        if name == "grouped_files":
            films = sorted(i["Name"][:6] for i in items if i.get("Type") == "Movie")
            results["grouping_hides_collected_films"] = check(
                [f for f in films if f in ("Film A", "Film B")], []
            )
        if name in ("files", "series"):
            results[f"{name}_listing_delivers_its_total"] = check(len(items), total)
            for start in range(0, total, 3):
                get("/Items", {**listing, "StartIndex": str(start), "Limit": "3"})
        if name == "files":
            for item in items:
                if (item.get("PartCount") or 1) > 1:
                    get(f"/Videos/{item['Id']}/AdditionalParts", {})
    return results, entries


def _mapper(media: Path) -> Callable[[str], str]:
    """Map Jellyfin's /media to this host and leave every other path as Jellyfin spells it."""

    return lambda path: (
        str(media / path.removeprefix("/media/")) if path.startswith("/media/") else path
    )


def decisions(
    adapter: JellyfinUserAdapter, admin, casey_hex: str, media: Path, add_library
) -> dict[str, object]:
    """Decide Casey's targets with every library mapped, then with an unmounted Music library."""

    local = _mapper(media)
    server = adapter.server()
    assert not isinstance(server, Unverified), server
    identity = ProviderIdentity(
        server.server_id, parse_jellyfin_user_id(casey_hex.replace("-", ""))
    )
    user = adapter.verify_user(identity)
    assert isinstance(user, JellyfinUserSnapshot), user
    folders = adapter.library_folders(server.server_id)
    assert not isinstance(folders, Unverified), folders
    catalog = adapter.catalog(server.server_id)
    results: dict[str, object] = {
        "adapter_catalog_builds": check(isinstance(catalog, Unverified), False)
    }
    if isinstance(catalog, Unverified):
        results["adapter_catalog_unverified"] = str(catalog)
        return results
    types = {item.item_type for item in catalog.items}
    wanted_types = {"Movie", "Episode", "Series", "Video", "Trailer", "Audio", "Photo"}
    results["catalog_types"] = check(sorted(types & wanted_types), sorted(wanted_types)) | {
        "all": sorted(types)
    }
    virtual = {
        v["Name"]: canonical_guid(v["ItemId"])
        for v in admin.call("GET", "/Library/VirtualFolders").json()
    }
    results["policy_ids_are_library_ids"] = check(
        user.media.library_ids, {virtual["Family"], virtual["Home"]}
    )
    index = JellyfinCatalogIndex(catalog, local)

    def path_target(path: str) -> MediaTarget:
        """A target that only its resolved path can match, keyed by a synthetic item GUID."""
        item = uuid.uuid5(uuid.NAMESPACE_URL, path).hex
        key = MediaKey(MediaSourceKind.JELLYFIN_LIBRARY, server.server_id, item)
        return MediaTarget(key=key, source=None, paths=frozenset({path}))

    missing = [p for p in library_files() if not index.match(path_target(local(f"/media/{p}")))]
    results["every_library_file_indexed"] = check(missing, [])
    movie = {i.name[:6]: i for i in catalog.items if i.item_type == "Movie"}
    shared = next(
        i for i in catalog.items if i.item_type == "Trailer" and "Shared Trailer" in (i.path or "")
    )
    outside = {
        name: str(media / "Elsewhere" / name)
        for name in (
            "Home.mkv",
            "Shared Trailer.mkv",
            "Shared Part.mkv",
            "Shared Song.m4a",
        )
    }
    inside = lambda path: local(f"/media/{path}")  # noqa: E731
    targets = {
        "path-film-a-closed": path_target(inside(LAYOUT_FILES["film-a"])),
        "path-film-c-open": path_target(inside(LAYOUT_FILES["film-c"])),
        "path-film-b-2160p-closed": path_target(inside(LAYOUT_FILES["film-b-2160p"])),
        "path-show-x-episode-closed": path_target(inside(f"{SHOW}/Season 01/Show X S01E02.mkv")),
        "path-home-clip-open": path_target(inside("Home/Clip.mkv")),
        "path-home-photo-open": path_target(inside("Home/Photo.jpg")),
        "path-family-trailer-open": path_target(inside(f"{FILM_C}/trailers/Family Trailer.mkv")),
        "path-movies-extra-closed": path_target(
            inside(f"{FILM_A}/behind the scenes/Making Of.mkv")
        ),
        "path-outside": path_target(outside["Home.mkv"]),
        "path-outside-shared-trailer": path_target(outside["Shared Trailer.mkv"]),
        "path-outside-shared-part": path_target(outside["Shared Part.mkv"]),
        "path-outside-shared-song": path_target(outside["Shared Song.m4a"]),
        "jellyfin-film-a": jellyfin_target(server.server_id, movie["Film A"], local),
        "jellyfin-film-c": jellyfin_target(server.server_id, movie["Film C"], local),
        "jellyfin-shared-trailer": jellyfin_target(server.server_id, shared, local),
    }
    subject = LinkedSubject(identity, ProviderLinkState.VERIFIED)
    wanted = {
        name: VERIFIED if name.endswith("-open") or name == "jellyfin-film-c" else CLOSED
        for name in targets
    }
    wanted["path-outside"] = MediaDenied(MediaDenial.UNMATCHED)

    def decide(roots: LibraryRoots) -> dict[str, str]:
        made = decide_media(
            subject,
            list(targets.values()),
            user=user,
            catalog=index,
            libraries=roots,
            items=adapter,
            now=datetime.now(UTC),
        )
        return {name: str(made[t.key]) for name, t in targets.items()}

    results["casey_decisions"] = check(
        decide(LibraryRoots(folders, local)), {k: str(v) for k, v in wanted.items()}
    )
    add_library(admin, "Music", "music", [], "/music")
    unmounted = adapter.library_folders(server.server_id)
    assert not isinstance(unmounted, Unverified), unmounted
    wanted["path-outside"] = MediaDenied(MediaDenial.UNRESOLVED)
    results["casey_decisions_with_unmounted_music"] = check(
        decide(LibraryRoots(unmounted, local)), {k: str(v) for k, v in wanted.items()}
    )
    return results


class MutatingTransport(httpx.HTTPTransport):
    """Before the second page of the file listing, add a movie and wait until Jellyfin counts it."""

    def __init__(self, admin, media: Path, observed: dict[str, object]):
        super().__init__()
        self.admin, self.media, self.observed = admin, media, observed

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if "MediaTypes" in params and params.get("StartIndex") == "3" and not self.observed:
            query = {**jellyfin_catalog.FILE_LISTING, "Limit": "0"}

            def count() -> int:
                return self.admin.call("GET", "/Items", params=query).json()["TotalRecordCount"]

            before = count()
            new = self.media / "Movies/Film D (2004) [tmdbid-9004]/Film D (2004) [tmdbid-9004].mkv"
            new.parent.mkdir(parents=True)
            shutil.copyfile(self.media / "plain.mkv", new)
            self.admin.call("POST", "/Library/Refresh")
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and count() == before:
                time.sleep(1)
            self.observed["total"] = [before, count()]
        return super().handle_request(request)


def paging(base: str, key: str, admin, media: Path) -> dict[str, object]:
    """Build the catalog in pages of three, then while a movie is added mid-listing."""

    results: dict[str, object] = {}
    page, jellyfin_catalog.CATALOG_PAGE = jellyfin_catalog.CATALOG_PAGE, 3
    try:
        with JellyfinUserAdapter(base, key) as adapter:
            paged = adapter.catalog(adapter.server().server_id)
            results["paged_catalog_builds"] = check(isinstance(paged, Unverified), False)
        observed: dict[str, object] = {}
        with JellyfinUserAdapter(
            base, key, transport=MutatingTransport(admin, media, observed)
        ) as adapter:
            server = adapter.server().server_id
            during = adapter.catalog(server)
            before, after = observed.get("total", (0, 0))
            results["mutation_total"] = observed
            if before == after:
                results["mutation_counted"] = check(after, before + 1)
                return results
            refused = Unverified(UnverifiedReason.INVALID_RESPONSE)
            results["catalog_during_change_refused"] = check(str(during), str(refused))
            settled = adapter.catalog(server)
            grown = (
                None if isinstance(settled, Unverified) else len(settled.items) - len(paged.items)
            )
            results["catalog_after_change_settles"] = check(grown, 1)
    finally:
        jellyfin_catalog.CATALOG_PAGE = page
    return results
