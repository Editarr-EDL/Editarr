"""MU-11: the catalog adapter against listings recorded from a real Jellyfin 12.0.0 server.

`fixtures/multi-user/jellyfin-catalog-v3/recording.json` holds unmodified
server-key responses over synthetic media with a local trailer, extras, a song,
a photo, collections, a merged and a stacked movie, and symlinks to files outside
every library, and the library folders with the Collections library. Grouped
controls show the same listings with collection grouping on. It was written by
`.github/skills/verify-editarr/scripts/media_access_live.py run --record`
against the pinned image. The replay serves a recorded body only for the exact
path and query it answered.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from scanner.integrations import jellyfin_catalog
from scanner.integrations.jellyfin_catalog import (
    FILE_LISTING,
    SERIES_LISTING,
    JellyfinCatalog,
    parse_catalog,
)
from scanner.integrations.jellyfin_user_snapshot import (
    InvalidPayload,
    Unverified,
    UnverifiedReason,
    canonical_guid,
)
from scanner.integrations.jellyfin_users import JellyfinUserAdapter

RECORDING = json.loads(
    (Path(__file__).parent / "fixtures/multi-user/jellyfin-catalog-v3/recording.json").read_text()
)
RESPONSES = RECORDING["responses"]
SERVER = canonical_guid(RESPONSES[0]["body"]["Id"])
RECORDED_AT = RECORDING["provenance"]["recordedAt"]


def _recorded(listing, start="0", limit="500"):
    query = {**listing, "StartIndex": start, "Limit": limit}
    return next(entry["body"] for entry in RESPONSES if entry["query"] == query)


FILES, SERIES = _recorded(FILE_LISTING), _recorded(SERIES_LISTING)
GROUPED_FILES = _recorded({**FILE_LISTING, "CollapseBoxSetItems": "true"})
GROUPED_SERIES = _recorded({**SERIES_LISTING, "CollapseBoxSetItems": "true"})
PARTS = {
    entry["path"].split("/")[2]: entry["body"]["Items"]
    for entry in RESPONSES
    if entry["path"].endswith("/AdditionalParts")
}


def _films(page):
    return sorted(item["Name"][:6] for item in page["Items"] if item["Type"] == "Movie")


def _replay(requests, reread=None, listing=FILE_LISTING):
    """Serve each recorded body for its exact query; `reread` answers the second
    first-page read of `listing`."""

    def answer(request):
        query = dict(request.url.params)
        requests.append(query)
        first = query.get("StartIndex") == "0" and query.items() >= listing.items()
        if reread is not None and first:
            if sum(seen.items() >= {**listing, "StartIndex": "0"}.items() for seen in requests) > 1:
                if isinstance(reread, httpx.Response):
                    return reread
                return httpx.Response(200, json=reread)
        for entry in RESPONSES:
            if entry["path"] == request.url.path and entry["query"] == query:
                return httpx.Response(entry["status"], json=entry["body"])
        return httpx.Response(400, json={})

    return JellyfinUserAdapter("http://jellyfin.test", "key", transport=httpx.MockTransport(answer))


def _starts(requests):
    return [(bool(query.get("MediaTypes")), query["StartIndex"]) for query in requests if query]


def test_the_recorded_unfiltered_listing_counts_an_item_it_never_delivers():
    first = next(entry for entry in RESPONSES if entry["path"] == "/Items")
    unfiltered = first["body"]
    assert "MediaTypes" not in first["query"]
    assert unfiltered["TotalRecordCount"] == len(unfiltered["Items"]) + 1
    with pytest.raises(InvalidPayload):
        parse_catalog([[unfiltered]], SERVER, datetime.fromisoformat(RECORDED_AT))


def test_the_recorded_file_listing_holds_trailers_extras_audio_and_photos_but_no_folders():
    assert RECORDING["provenance"]["jellyfinVersion"] == "12.0.0"
    kinds = {(item["Type"], item.get("ExtraType")) for item in FILES["Items"]}
    assert {("Trailer", "Trailer"), ("Video", "BehindTheScenes"), ("Video", "ThemeVideo")} <= kinds
    assert {"Movie", "Episode", "Video", "Trailer", "Audio", "Photo"} <= {kind for kind, _ in kinds}
    assert {item["MediaType"] for item in FILES["Items"]} == {"Video", "Audio", "Photo"}
    assert [item["Type"] for item in SERIES["Items"]] == ["Series"]


def test_recorded_grouping_hides_collected_films_and_series_unless_turned_off():
    assert {"Film A", "Film B"} <= set(_films(FILES))
    assert not {"Film A", "Film B"} & set(_films(GROUPED_FILES))
    assert "BoxSet" in {item["Type"] for item in GROUPED_FILES["Items"]}
    assert [item["Type"] for item in GROUPED_SERIES["Items"]] == ["BoxSet"]
    assert GROUPED_FILES["TotalRecordCount"] < FILES["TotalRecordCount"]


def test_the_recorded_stacked_movie_lists_its_later_part_only_through_additional_parts():
    stacked = [item for item in FILES["Items"] if (item.get("PartCount") or 1) > 1]
    assert [item["PartCount"] for item in stacked] == [2]
    (part,) = PARTS[stacked[0]["Id"]]
    assert part["Path"].endswith(" - cd2.mkv")
    listed = {s["Path"] for item in FILES["Items"] for s in item.get("MediaSources") or []}
    assert part["Path"] not in listed


def test_the_adapter_builds_a_catalog_from_recorded_jellyfin_12_listings():
    requests = []
    with _replay(requests) as adapter:
        listed = adapter.catalog(SERVER)
    assert isinstance(listed, JellyfinCatalog)
    expected = [item["Id"] for item in FILES["Items"] + SERIES["Items"]]
    assert [item.id for item in listed.items] == expected
    recorded = {
        path
        for item in FILES["Items"]
        for path in (item.get("Path"), *(s["Path"] for s in item.get("MediaSources", [])))
        if path
    }
    listed_paths = {
        path for item in listed.items for path in (item.path, *(v.path for v in item.versions))
    }
    assert any("/trailers/Shared Trailer.mkv" in path for path in recorded)
    assert recorded | {part["Path"] for parts in PARTS.values() for part in parts} <= listed_paths
    assert _starts(requests) == [(True, "0"), (True, "0"), (False, "0"), (False, "0")]


def test_the_adapter_pages_recorded_listings_then_rereads_each_first_page(monkeypatch):
    monkeypatch.setattr(jellyfin_catalog, "CATALOG_PAGE", 3)
    requests = []
    with _replay(requests) as adapter:
        listed = adapter.catalog(SERVER)
    assert [item.id for item in listed.items] == [i["Id"] for i in FILES["Items"] + SERIES["Items"]]
    pages = [(True, str(start)) for start in range(0, FILES["TotalRecordCount"], 3)]
    assert _starts(requests) == [*pages, (True, "0"), (False, "0"), (False, "0")]


def _first_page(listing, change):
    return change(json.loads(json.dumps(_recorded(listing, limit="3"))))


@pytest.mark.parametrize("listing", [FILE_LISTING, SERIES_LISTING], ids=["files", "series"])
@pytest.mark.parametrize(
    "change",
    [
        lambda page: {**page, "TotalRecordCount": page["TotalRecordCount"] + 1},
        lambda page: {**page, "Items": page["Items"][1:] + page["Items"][:1]},
        lambda page: {**page, "Items": page["Items"][:-1]},
        lambda page: {**page, "StartIndex": 1},
    ],
    ids=["total-changed", "items-shifted", "page-shrank", "wrong-place"],
)
def test_a_first_page_that_changed_while_the_listing_was_read_rejects_it(
    monkeypatch, listing, change
):
    monkeypatch.setattr(jellyfin_catalog, "CATALOG_PAGE", 3)
    reread = _first_page(listing, change)
    if reread == _first_page(listing, lambda page: page):
        pytest.skip("a one-item page cannot shift")
    with _replay([], reread, listing) as adapter:
        assert adapter.catalog(SERVER) == Unverified(UnverifiedReason.INVALID_RESPONSE)


@pytest.mark.parametrize("listing", [FILE_LISTING, SERIES_LISTING], ids=["files", "series"])
def test_a_failed_reread_of_a_first_page_rejects_the_listing(monkeypatch, listing):
    monkeypatch.setattr(jellyfin_catalog, "CATALOG_PAGE", 3)
    with _replay([], httpx.Response(503), listing) as adapter:
        assert adapter.catalog(SERVER) == Unverified(UnverifiedReason.SERVICE_ERROR)
