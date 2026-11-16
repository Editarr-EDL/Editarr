"""Small, server-side-only adapter for the Jellyfin REST API."""

from __future__ import annotations

from urllib.parse import quote

import httpx

from scanner.modeling.media_identity import MediaIdentity, normalize_jellyfin
from scanner.models import JellyfinLibrary, JellyfinMediaItem, JellyfinMediaVersion

from .jellyfin_errors import JellyfinError, JellyfinNotFound
from .jellyfin_paths import library_of
from .jellyfin_server_url import normalize_server_url


def media_browser_authorization(**fields: str) -> str:
    """Build a `MediaBrowser` Authorization header, percent-encoding each value.

    Jellyfin 12 no longer accepts X-Emby-Token by default. The MediaBrowser
    scheme also works with older Jellyfin servers.
    """
    return "MediaBrowser " + ", ".join(
        f'{name}="{quote(value, safe="")}"' for name, value in fields.items()
    )


class JellyfinClient:
    """Translate Jellyfin payloads into the minimum stable Editarr shapes."""

    def __init__(self, server_url: str, api_key: str, client: httpx.Client | None = None):
        if not api_key.strip():
            raise JellyfinError("Enter a Jellyfin API key")
        self.server_url = normalize_server_url(server_url)
        self.api_key = api_key.strip()
        self._client = client or httpx.Client(timeout=httpx.Timeout(10.0))
        self._owns_client = client is None
        self._series_cache: dict[str, dict[str, object]] = {}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> JellyfinClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _headers(self, accept: str) -> dict[str, str]:
        return {
            "Authorization": media_browser_authorization(Token=self.api_key),
            "Accept": accept,
        }

    def test_connection(self) -> None:
        """Use an authenticated endpoint so an invalid key cannot look healthy."""
        self._get_json("/Library/VirtualFolders")

    def libraries(self) -> list[JellyfinLibrary]:
        """Fetch available Jellyfin libraries and normalize their public metadata."""
        payload = self._get_json("/Library/VirtualFolders")
        if not isinstance(payload, list):
            raise JellyfinError("Jellyfin returned an invalid libraries response")
        libraries: list[JellyfinLibrary] = []
        for item in payload:
            if not isinstance(item, dict) or not item.get("ItemId") or not item.get("Name"):
                continue
            locations = item.get("Locations")
            libraries.append(
                JellyfinLibrary(
                    id=str(item["ItemId"]),
                    name=str(item["Name"]),
                    collection_type=item.get("CollectionType"),
                    locations=(
                        [str(location) for location in locations]
                        if isinstance(locations, list)
                        else []
                    ),
                )
            )
        return libraries

    def items(
        self,
        library_id: str,
        *,
        start_index: int = 0,
        limit: int = 100,
        recursive: bool = True,
        include_item_types: tuple[str, ...] = (),
    ) -> list[JellyfinMediaItem]:
        """Fetch a page of library items using the supplied parent and paging options."""
        payload = self._get_json(
            "/Items",
            params={
                "ParentId": library_id,
                "Recursive": str(recursive).lower(),
                "StartIndex": str(start_index),
                "Limit": str(limit),
                "Fields": (
                    "Path,Overview,SortName,PrimaryImageAspectRatio,DateCreated,"
                    "MediaStreams,ParentId,ParentIndexNumber,ProviderIds,SeriesId"
                ),
                **(
                    {"IncludeItemTypes": ",".join(include_item_types)} if include_item_types else {}
                ),
            },
            names_id=True,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("Items"), list):
            raise JellyfinError("Jellyfin returned an invalid library items response")
        records = [item for item in payload["Items"] if isinstance(item, dict)]
        contexts = self._series_contexts(records)
        return [
            self._media_item(item, library_id, series=contexts.get(str(item.get("SeriesId"))))
            for item in records
        ]

    def all_items(
        self,
        library_id: str,
        *,
        recursive: bool = True,
        include_item_types: tuple[str, ...] = (),
    ) -> list[JellyfinMediaItem]:
        """Retrieve every page for a bounded Jellyfin catalog query."""
        items: list[JellyfinMediaItem] = []
        page_size = 500
        while True:
            page = self.items(
                library_id,
                start_index=len(items),
                limit=page_size,
                recursive=recursive,
                include_item_types=include_item_types,
            )
            items.extend(page)
            if len(page) < page_size:
                return items

    def children(self, item_id: str) -> list[JellyfinMediaItem]:
        return self.items(item_id, recursive=False, limit=500)

    def seasons(self, series_id: str) -> list[JellyfinMediaItem]:
        """Use Jellyfin's series endpoint instead of traversing episode data."""
        payload = self._get_json(
            f"/Shows/{quote(series_id, safe='')}/Seasons",
            params={
                "Fields": (
                    "Path,Overview,SortName,PrimaryImageAspectRatio,DateCreated,"
                    "ParentId,ParentIndexNumber"
                )
            },
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("Items"), list):
            raise JellyfinError("Jellyfin returned an invalid seasons response")
        return [
            self._media_item(item, str(item.get("LibraryId") or ""))
            for item in payload["Items"]
            if isinstance(item, dict)
        ]

    def item(self, item_id: str) -> JellyfinMediaItem:
        """Fetch one item through Jellyfin's collection query.

        Some Jellyfin deployments reject the singular `/Items/{id}` route for
        a configured API key even though the same key may browse `/Items`.
        Using `Ids` keeps catalog, detail, and hierarchy requests on the same
        authenticated endpoint.
        """
        payload = self._get_json(
            "/Items",
            params={
                "Ids": item_id,
                "Limit": "1",
                "Fields": (
                    "Path,Overview,SortName,PrimaryImageAspectRatio,DateCreated,"
                    "MediaStreams,MediaSources,MediaSourceCount,ParentId,"
                    "ParentIndexNumber,ProviderIds,SeriesId"
                ),
            },
            names_id=True,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("Items"), list):
            raise JellyfinError("Jellyfin returned an invalid media item response")
        if not payload["Items"]:
            raise JellyfinNotFound("Jellyfin did not return the requested media item")
        if not isinstance(payload["Items"][0], dict):
            raise JellyfinError("Jellyfin returned an invalid media item response")
        item = payload["Items"][0]
        contexts = self._series_contexts([item])
        library = str(item.get("LibraryId") or "")
        if not library and item.get("Path"):
            # Jellyfin 12 omits LibraryId here; the library whose folder holds the file owns it.
            library = library_of(str(item["Path"]), self.libraries()) or ""
        return self._media_item(
            item,
            library,
            include_versions=True,
            series=contexts.get(str(item.get("SeriesId"))),
        )

    def _series_contexts(self, items: list[dict[str, object]]) -> dict[str, dict[str, object]]:
        """Resolve linked series in one optional lookup without reusing episode IDs."""
        for item in items:
            if item.get("Type") == "Series" and isinstance(item.get("Id"), str):
                self._series_cache[item["Id"]] = item
        identifiers = {
            item["SeriesId"]
            for item in items
            if item.get("Type") == "Episode"
            and isinstance(item.get("SeriesId"), str)
            and item["SeriesId"].isalnum()
            and len(item["SeriesId"]) <= 64
        } - self._series_cache.keys()
        if identifiers:
            try:
                payload = self._get_json(
                    "/Items",
                    params={
                        "Ids": ",".join(sorted(identifiers)),
                        "Limit": str(len(identifiers)),
                        "Fields": "ProviderIds",
                    },
                )
                records = payload.get("Items") if isinstance(payload, dict) else None
                if isinstance(records, list):
                    for record in records:
                        if (
                            isinstance(record, dict)
                            and record.get("Type") == "Series"
                            and record.get("Id") in identifiers
                        ):
                            self._series_cache[record["Id"]] = record
            except JellyfinError:
                pass
        return self._series_cache

    def primary_image(self, item_id: str, image_tag: str | None = None) -> tuple[bytes, str] | None:
        """Fetch poster bytes through Editarr so clients never need the API key."""
        try:
            response = self._client.get(
                f"{self.server_url}/Items/{quote(item_id, safe='')}/Images/Primary",
                params={"tag": image_tag} if image_tag else None,
                headers=self._headers("image/*"),
            )
        except httpx.TimeoutException as exc:
            raise JellyfinError("Timed out connecting to Jellyfin") from exc
        except httpx.HTTPError as exc:
            raise JellyfinError("Unable to connect to Jellyfin") from exc
        if response.status_code == 404:
            return None
        if response.status_code in {401, 403}:
            raise JellyfinError("Jellyfin rejected the configured API key")
        if response.status_code >= 400:
            raise JellyfinError(f"Jellyfin image request failed ({response.status_code})")
        content_type = response.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
        if not content_type.startswith("image/"):
            raise JellyfinError("Jellyfin returned an invalid poster image")
        return response.content, content_type

    def _get_json(
        self, path: str, params: dict[str, str] | None = None, *, names_id: bool = False
    ) -> object:
        """GET a Jellyfin endpoint with server-side credentials and map safe errors.

        With `names_id`, Jellyfin rejecting the request (400 or 404) means the
        library or item ID it names does not exist: `JellyfinNotFound`.
        """
        try:
            response = self._client.get(
                f"{self.server_url}{path}",
                params=params,
                headers=self._headers("application/json"),
            )
        except httpx.TimeoutException as exc:
            raise JellyfinError("Timed out connecting to Jellyfin") from exc
        except httpx.HTTPError as exc:
            raise JellyfinError("Unable to connect to Jellyfin") from exc

        if response.status_code in {401, 403}:
            raise JellyfinError("Jellyfin rejected the configured API key")
        if names_id and response.status_code in {400, 404}:
            raise JellyfinNotFound("Jellyfin did not find the requested library or item")
        if response.status_code >= 400:
            raise JellyfinError(f"Jellyfin request failed ({response.status_code})")
        try:
            return response.json()
        except ValueError as exc:
            raise JellyfinError("Jellyfin returned an invalid response") from exc

    @staticmethod
    def _media_item(
        item: dict[str, object],
        library_id: str,
        *,
        include_versions: bool = False,
        series: dict[str, object] | None = None,
    ) -> JellyfinMediaItem:
        """Normalize one Jellyfin item and its media sources into catalog metadata."""
        item_id = item.get("Id")
        name = item.get("Name")
        item_type = item.get("Type")
        if not item_id or not name or not item_type:
            raise JellyfinError("Jellyfin returned an item without required metadata")
        image_tags = item.get("ImageTags")
        media_streams = item.get("MediaStreams")
        streams = (
            [dict(stream) for stream in media_streams if isinstance(stream, dict)]
            if isinstance(media_streams, list)
            else []
        )
        runtime_ticks = int(item["RunTimeTicks"]) if item.get("RunTimeTicks") else None
        sources = item.get("MediaSources")
        provider_ids = item.get("ProviderIds")
        media_item = JellyfinMediaItem(
            id=str(item_id),
            library_id=library_id,
            parent_id=str(item["ParentId"]) if item.get("ParentId") else None,
            item_type=str(item_type),
            name=str(name),
            sort_name=str(item["SortName"]) if item.get("SortName") else None,
            year=int(item["ProductionYear"]) if item.get("ProductionYear") else None,
            official_rating=str(item["OfficialRating"]) if item.get("OfficialRating") else None,
            season_number=(
                int(item["ParentIndexNumber"])
                if item.get("ParentIndexNumber") is not None
                else None
            ),
            index_number=int(item["IndexNumber"]) if item.get("IndexNumber") is not None else None,
            overview=str(item["Overview"]) if item.get("Overview") else None,
            path=str(item["Path"]) if item.get("Path") else None,
            runtime_ticks=runtime_ticks,
            duration_seconds=runtime_ticks / 10_000_000 if runtime_ticks else None,
            resolution=_resolution_category(streams),
            media_format=_media_format(item.get("Container"), streams),
            media_provider_id=_media_provider_id(str(item_type), provider_ids),
            media_source_count=(
                int(item["MediaSourceCount"]) if item.get("MediaSourceCount") is not None else None
            ),
            versions=(
                [
                    _media_version(
                        source,
                        media_identity=normalize_jellyfin(item, series=series, source=source)
                        if item_type in ("Movie", "Series", "Episode")
                        else None,
                    )
                    for source in sources
                    if isinstance(source, dict)
                ]
                if include_versions and isinstance(sources, list)
                else []
            ),
            media_streams=streams,
            image_tags=image_tags if isinstance(image_tags, dict) else {},
            media_identity=normalize_jellyfin(item, series=series)
            if item_type in ("Movie", "Series", "Episode")
            else None,
        )
        media_item._detail_loaded = include_versions
        return media_item


def _resolution_category(streams: list[dict[str, object]]) -> str | None:
    """Classify a media source resolution for display and source selection."""
    heights = [stream.get("Height") for stream in streams if stream.get("Type") == "Video"]
    height = max((value for value in heights if isinstance(value, int)), default=None)
    if height is None:
        return None
    if height >= 2160:
        return "4K"
    if height >= 1440:
        return "1440p"
    if height >= 1080:
        return "1080p"
    if height >= 720:
        return "720p"
    if height >= 480:
        return "480p"
    return "SD"


def _media_format(container: object, streams: list[dict[str, object]]) -> str | None:
    video = next((stream for stream in streams if stream.get("Type") == "Video"), None)
    video_codec = video.get("Codec") if video else None
    values = [
        str(container).upper() if container else None,
        str(video_codec).upper() if video_codec else None,
    ]
    return " · ".join(value for value in values if value) or None


def _media_provider_id(item_type: str, provider_ids: object) -> str | None:
    if not isinstance(provider_ids, dict):
        return None
    provider_key = "Tmdb" if item_type == "Movie" else "Tvdb" if item_type == "Episode" else None
    value = provider_ids.get(provider_key) if provider_key else None
    if isinstance(value, str) and value.strip():
        return f"{provider_key.casefold()}:{value.strip()}"
    return None


def _media_version(
    source: dict[str, object], *, media_identity: MediaIdentity | None = None
) -> JellyfinMediaVersion:
    """Normalize one Jellyfin media source into its physical file version."""
    streams = source.get("MediaStreams")
    normalized_streams = (
        [dict(stream) for stream in streams if isinstance(stream, dict)]
        if isinstance(streams, list)
        else []
    )
    runtime_ticks = int(source["RunTimeTicks"]) if source.get("RunTimeTicks") else None
    size = source.get("Size")
    return JellyfinMediaVersion(
        id=str(source["Id"]) if source.get("Id") else None,
        name=str(source["Name"]) if source.get("Name") else None,
        scene_name=(
            str(source["SceneName"])
            if source.get("SceneName")
            else str(source["Name"])
            if source.get("Name")
            else None
        ),
        release_group=(str(source["ReleaseGroup"]) if source.get("ReleaseGroup") else None),
        edition=str(source["Edition"]) if source.get("Edition") else None,
        path=str(source["Path"]) if source.get("Path") else None,
        container=str(source["Container"]) if source.get("Container") else None,
        resolution=_resolution_category(normalized_streams),
        media_format=_media_format(source.get("Container"), normalized_streams),
        duration_seconds=runtime_ticks / 10_000_000 if runtime_ticks else None,
        size_bytes=int(size) if isinstance(size, int) else None,
        media_identity=media_identity,
    )
