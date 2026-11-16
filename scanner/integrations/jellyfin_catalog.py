"""Every media file item and series a Jellyfin server key lists, read whole or not at all.

Media access decides which Jellyfin items a file corresponds to. A listing
that misses an item can drop a restriction, so the only way to obtain a
`JellyfinCatalog` is to parse complete paged `/Items` listings of one server.

One listing selects items by media type, so every video, audio, photo, and book
file is indexed whatever its item type: local trailers, extras, and kinds
Editarr does not know. A second lists `Series` for episode context. Both turn
off collection grouping, which otherwise replaces a collection's movies and
series with its `BoxSet`. A stacked movie lists only its first part, so each
listed `PartCount` is matched by its `/Videos/{id}/AdditionalParts`. Items of
media type `Unknown` are folders, series, collections, and playlists; a playlist
file lists other items and is not media Editarr scans or serves. Jellyfin 12.0.0
can count a system folder in an unfiltered listing that it never delivers
(recorded in tests/fixtures/multi-user/jellyfin-catalog-v3). Jellyfin offers no
sort by ID, so pages follow `DateCreated,SortName` and each listing's first page
is read again after its last; a remove-and-add beyond the first page that keeps
the total can still go unseen.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from scanner.models import JellyfinMediaItem, JellyfinMediaVersion
from scanner.security.access.identity import JellyfinServerId

from .jellyfin import JellyfinClient, JellyfinError
from .jellyfin_user_snapshot import InvalidPayload, Unverified, canonical_guid

CATALOG_PAGE = 500
MAX_CATALOG_ITEMS = 250_000
_LISTED = {
    "Recursive": "true",
    "Fields": "Path,ProviderIds,MediaSources,SeriesId,ParentIndexNumber",
    "EnableImages": "false",
    "EnableUserData": "false",
    "SortBy": "DateCreated,SortName",
    "SortOrder": "Ascending",
    "CollapseBoxSetItems": "false",
    "EnableTotalRecordCount": "true",
}
FILE_LISTING = {**_LISTED, "MediaTypes": "Video,Audio,Photo,Book"}
"""Every media file item: each media type but `Unknown`."""
SERIES_LISTING = {**_LISTED, "IncludeItemTypes": "Series"}
CATALOG_LISTINGS = (FILE_LISTING, SERIES_LISTING)
_SEAL = object()

PageReader = Callable[[str, dict[str, str]], object]
"""Reads one server-key path with a query; returns the parsed body or an `Unverified`."""


@dataclass(frozen=True)
class JellyfinCatalog:
    """Every media file item and series of one server, with every version and part path.

    Only `parse_catalog` builds one, so holding it proves complete listings.
    """

    server_id: JellyfinServerId
    items: tuple[JellyfinMediaItem, ...]
    read_at: datetime
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise TypeError("JellyfinCatalog comes only from a complete listing")


def next_start(page: object, start: int) -> int | None:
    """Check one listing page requested at `start`; the next start, or None when complete."""

    if not isinstance(page, dict) or not isinstance(page.get("Items"), list):
        raise InvalidPayload("A listing page must hold items")
    total, echoed = page.get("TotalRecordCount"), page.get("StartIndex")
    if type(total) is not int or type(echoed) is not int or echoed != start:
        raise InvalidPayload("A listing page must state its place in the listing")
    if total > MAX_CATALOG_ITEMS:
        raise InvalidPayload("The listing is larger than Editarr reads")
    listed = start + len(page["Items"])
    if listed > total or (listed < total and not page["Items"]):
        raise InvalidPayload("The listing changed while it was read")
    return None if listed == total else listed


def catalog_query(listing: Mapping[str, str], start: int) -> dict[str, str]:
    """The query for the page of `listing` that begins at `start`."""

    return {**listing, "StartIndex": str(start), "Limit": str(CATALOG_PAGE)}


def unchanged(first: object, reread: object) -> None:
    """Refuse a listing whose first page or total changed while its later pages were read."""

    next_start(reread, 0)
    before, after = (
        (page["TotalRecordCount"], [_listed_id(item) for item in page["Items"]])
        for page in (first, reread)
    )
    if before != after:
        raise InvalidPayload("The listing changed while it was read")


def read_catalog(
    read: PageReader, server_id: JellyfinServerId, clock: Callable[[], datetime]
) -> JellyfinCatalog | Unverified:
    """Read every page of each listing, then each first page again, then stacked parts.

    Raises `InvalidPayload` for an inconsistent listing; a failed read is returned.
    """

    listings: list[list[object]] = []
    for listing in CATALOG_LISTINGS:
        pages = _read_listing(lambda query: read("/Items", query), listing)
        if isinstance(pages, Unverified):
            return pages
        listings.append(pages)
    parts: dict[str, object] = {}
    for record in _listed(listings[0]):
        if isinstance(record, dict) and _part_count(record) > 1:
            canonical_guid(record.get("Id"))
            found = read(f"/Videos/{record['Id']}/AdditionalParts", {})
            if isinstance(found, Unverified):
                return found
            if not isinstance(found, dict) or not isinstance(found.get("Items"), list):
                raise InvalidPayload("A stacked item's parts must be a list")
            parts[str(record["Id"])] = found["Items"]
    return parse_catalog(listings, server_id, clock(), parts)


def _read_listing(
    read_page: Callable[[dict[str, str]], object], listing: Mapping[str, str]
) -> list[object] | Unverified:
    pages: list[object] = []
    start: int | None = 0
    while start is not None:
        page = read_page(catalog_query(listing, start))
        if isinstance(page, Unverified):
            return page
        pages.append(page)
        start = next_start(page, start)
    reread = read_page(catalog_query(listing, 0))
    if isinstance(reread, Unverified):
        return reread
    unchanged(pages[0], reread)
    return pages


def parse_catalog(
    listings: Sequence[Sequence[object]],
    server_id: JellyfinServerId,
    read_at: datetime,
    parts: Mapping[str, object] | None = None,
) -> JellyfinCatalog:
    """Parse the pages of each listing and the later parts of each stacked item.

    `parts` maps a stacked item's ID to its `AdditionalParts` items. A gap, overlap,
    malformed item, or part count that its parts do not match rejects everything.
    """

    records = [record for pages in listings for record in _listed(pages)]
    if not all(isinstance(record, dict) for record in records):
        raise InvalidPayload("A listed item must be an object")
    ids = [canonical_guid(record.get("Id")) for record in records]
    if len(set(ids)) != len(ids):
        raise InvalidPayload("The listing repeats an item")
    parts = parts or {}
    series = {record["Id"]: record for record in records if record.get("Type") == "Series"}
    return JellyfinCatalog(
        server_id,
        tuple(_item(record, series, parts.get(str(record["Id"]))) for record in records),
        read_at,
        _SEAL,
    )


def _listed(pages: Sequence[object]) -> list[object]:
    records: list[object] = []
    totals = set()
    start: int | None = 0
    for page in pages:
        if start is None:
            raise InvalidPayload("The listing continues past its total")
        start = next_start(page, start)
        totals.add(page["TotalRecordCount"])
        records.extend(page["Items"])
    if start is not None or len(totals) != 1:
        raise InvalidPayload("The listing is incomplete")
    return records


def _listed_id(record: object) -> object:
    return record.get("Id") if isinstance(record, dict) else None


def _part_count(record: object) -> int:
    count = record.get("PartCount", 1) if isinstance(record, dict) else 1
    if count is None:
        return 1
    if type(count) is not int or count < 1:
        raise InvalidPayload("A listed part count must be a positive number")
    return count


def _part(found: object) -> JellyfinMediaVersion:
    if not isinstance(found, dict) or not isinstance(found.get("Path"), str):
        raise InvalidPayload("A stacked item's part must have a path")
    return JellyfinMediaVersion(
        id=str(found["Id"]) if found.get("Id") else None, path=found["Path"]
    )


def _item(
    record: dict[str, object], series: dict[object, dict[str, object]], parts: object
) -> JellyfinMediaItem:
    later = [] if parts is None else parts
    if not isinstance(later, list) or len(later) != _part_count(record) - 1:
        raise InvalidPayload("A stacked item's parts do not match its part count")
    try:
        context = None
        if record.get("Type") == "Episode":
            context = series.get(record.get("SeriesId"))
            if context is None:
                raise InvalidPayload("A listed episode names a series the listing lacks")
        item = JellyfinClient._media_item(record, "", include_versions=True, series=context)
    except (JellyfinError, TypeError, ValueError) as error:
        raise InvalidPayload("A listed item is malformed") from error
    return item.model_copy(update={"versions": [*item.versions, *map(_part, later)]})
