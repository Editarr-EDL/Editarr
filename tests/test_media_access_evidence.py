"""MU-11: linked decisions rest only on complete, current evidence of the linked server.

The catalog must come from one complete `/Items` listing, and the user's
Jellyfin policy closes libraries on its own: a file in a library the policy
does not open is restricted even when no catalog item names it. The adapter
cases drive the real MU-08 adapter over a scripted transport shaped after
Jellyfin 12.0.0's paged `QueryResult`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from scanner.integrations import jellyfin_catalog
from scanner.integrations.jellyfin_catalog import JellyfinCatalog, parse_catalog
from scanner.integrations.jellyfin_user_snapshot import (
    InvalidPayload,
    ItemAccess,
    Unverified,
    UnverifiedReason,
)
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDenial, MediaDenied
from scanner.security.media_access.matching import jellyfin_target
from scanner.security.media_access.resolver import OwnerSubject
from scanner.security.media_access.service import decide_media
from tests.jellyfin_user_boundary import NOW, PRIMARY, REPLACEMENT, scripted_transport
from tests.media_access_support import (
    EPISODE,
    FAMILY_FILM,
    FEATURE,
    JELLYFIN_ITEMS,
    SERIES,
    TARGETS,
    catalog,
    jellyfin_items,
    jellyfin_user,
    libraries,
    listing,
    record,
    resolve,
)
from tests.test_media_access_matrix import CASEY, JELLYFIN, Reader, linked

RECORDS = [record(item) for item in JELLYFIN_ITEMS.values()]
STALE = NOW - timedelta(hours=24, seconds=1)
VERIFIED = MediaAllowed(MediaBasis.JELLYFIN_VERIFIED)
FAMILY_PATH = "/jellyfin/family/Synthetic Family Film.mkv"


def _decide(name="path-family-film", subject=None, **evidence):
    target = TARGETS[name]
    reader = Reader(CASEY)
    found = {"user": jellyfin_user(), "catalog": catalog(), "libraries": libraries()} | evidence
    decision = decide_media(subject or linked("Casey"), [target], items=reader, now=NOW, **found)[
        target.key
    ]
    return decision, reader


def test_a_catalog_comes_only_from_a_complete_listing():
    with pytest.raises(TypeError):
        JellyfinCatalog(PRIMARY, (), NOW)
    paged = parse_catalog([listing(RECORDS, page=4)], PRIMARY, NOW)
    assert sorted(item.id for item in paged.items) == sorted(JELLYFIN_ITEMS)


def _pages(**change):
    pages = listing(RECORDS, page=4)
    return [pages[0], {**pages[1], **change}]


@pytest.mark.parametrize(
    "pages",
    [
        [],
        listing(RECORDS, page=4)[:1],
        _pages(StartIndex=3),
        _pages(TotalRecordCount=7),
        _pages(Items=[]),
        [*listing(RECORDS, page=4), {"Items": [], "TotalRecordCount": 6, "StartIndex": 6}],
        listing([*RECORDS, RECORDS[0]]),
        listing([item for item in RECORDS if item["Id"] != SERIES]),
        listing([*RECORDS[:-1], {**RECORDS[-1], "Id": "not-a-guid"}]),
        [{**listing(RECORDS)[0], "TotalRecordCount": "6"}],
        [{**listing(RECORDS, page=4)[0], "TotalRecordCount": 8}, listing(RECORDS, page=4)[1]],
    ],
    ids=[
        "no-pages",
        "first-page-only",
        "overlap",
        "total-changed",
        "short-page",
        "past-the-end",
        "repeated-item",
        "episode-without-series",
        "bad-id",
        "total-not-a-number",
        "first-total-differs",
    ],
)
def test_any_gap_or_inconsistency_rejects_the_whole_listing(pages):
    with pytest.raises(InvalidPayload):
        parse_catalog([pages], PRIMARY, NOW)


def test_a_listing_larger_than_the_bound_is_refused(monkeypatch):
    monkeypatch.setattr(jellyfin_catalog, "MAX_CATALOG_ITEMS", len(RECORDS) - 1)
    with pytest.raises(InvalidPayload):
        parse_catalog([listing(RECORDS)], PRIMARY, NOW)


_items = jellyfin_items(RECORDS)


def _adapter(answer, servers=("primary",)):
    transport = scripted_transport(answer, servers)
    return JellyfinUserAdapter(
        "http://jellyfin.test", "key", transport=transport, clock=lambda: NOW
    )


def test_the_adapter_lists_every_page_of_the_linked_server(monkeypatch):
    monkeypatch.setattr(jellyfin_catalog, "CATALOG_PAGE", 4)
    with _adapter(_items) as adapter:
        listed = adapter.catalog(PRIMARY)
    assert (listed.server_id, listed.read_at, len(listed.items)) == (PRIMARY, NOW, 6)
    episode = next(item for item in listed.items if item.id == EPISODE)
    assert episode.media_identity.series_identifiers[0].value == "7001"


def test_the_adapter_stops_at_a_page_that_ends_the_listing_early():
    requests = []

    def empty(request):
        requests.append(request)
        if len(requests) > 3:
            return httpx.Response(500)
        start = int(request.url.params["StartIndex"])
        return httpx.Response(200, json={"Items": [], "TotalRecordCount": 6, "StartIndex": start})

    with _adapter(empty) as adapter:
        assert adapter.catalog(PRIMARY) == Unverified(UnverifiedReason.INVALID_RESPONSE)
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("server_id", "servers", "answer", "reason"),
    [
        (PRIMARY, ("primary", "replacement"), _items, UnverifiedReason.SERVER_MISMATCH),
        (REPLACEMENT, ("primary",), _items, UnverifiedReason.SERVER_MISMATCH),
        (PRIMARY, ("primary",), lambda _r: httpx.Response(500), UnverifiedReason.SERVICE_ERROR),
        (
            PRIMARY,
            ("primary",),
            lambda _r: httpx.Response(200, json={**listing(RECORDS)[0], "TotalRecordCount": 9}),
            UnverifiedReason.INVALID_RESPONSE,
        ),
    ],
    ids=["replaced-during-listing", "another-server", "server-error", "listing-changed"],
)
def test_the_adapter_reports_an_incomplete_or_foreign_listing_as_unverified(
    server_id, servers, answer, reason
):
    with _adapter(answer, servers) as adapter:
        assert adapter.catalog(server_id) == Unverified(reason)


@pytest.mark.parametrize(
    ("evidence", "denial"),
    [
        ({"catalog": catalog(server=REPLACEMENT)}, MediaDenial.UNRESOLVED),
        ({"catalog": catalog(read_at=STALE)}, MediaDenial.VERIFICATION_EXPIRED),
        ({"catalog": None}, MediaDenial.UNRESOLVED),
        ({"catalog": Unverified(UnverifiedReason.TIMEOUT)}, MediaDenial.UNRESOLVED),
        ({"catalog": Unverified(UnverifiedReason.SERVER_MISMATCH)}, MediaDenial.SERVER_REPLACED),
        ({"user": jellyfin_user("Blake")}, MediaDenial.UNRESOLVED),
        ({"user": jellyfin_user(verified_at=STALE)}, MediaDenial.VERIFICATION_EXPIRED),
        ({"user": None}, MediaDenial.UNRESOLVED),
        ({"user": Unverified(UnverifiedReason.SERVER_MISMATCH)}, MediaDenial.SERVER_REPLACED),
    ],
    ids=[
        "foreign-catalog",
        "stale-catalog",
        "no-catalog",
        "failed-catalog",
        "catalog-saw-replacement",
        "another-users-policy",
        "stale-policy",
        "no-policy",
        "policy-saw-replacement",
    ],
)
def test_evidence_not_bound_to_the_linked_server_and_user_denies_without_reading(evidence, denial):
    decision, reader = _decide(**evidence)
    assert decision == MediaDenied(denial)
    assert reader.requests == []


def test_a_closed_library_restricts_a_file_no_catalog_item_names():
    without_feature = catalog([item for item in RECORDS if item["Id"] != FEATURE])
    casey, reader = _decide("path-alternate-version", catalog=without_feature)
    blake, _ = _decide(
        "path-alternate-version",
        subject=linked("Blake"),
        user=jellyfin_user("Blake"),
        catalog=without_feature,
    )
    assert casey == MediaDenied(MediaDenial.NOT_IN_SCOPE)
    assert reader.requests == []
    assert blake == MediaDenied(MediaDenial.LIBRARY_UNMATCHED)


def test_closing_a_library_in_jellyfin_restricts_an_item_jellyfin_still_opens():
    casey = jellyfin_user()
    closed = replace(casey, media=replace(casey.media, library_ids=frozenset()))
    assert _decide()[0] == MediaAllowed(MediaBasis.JELLYFIN_VERIFIED)
    assert _decide(user=closed)[0] == MediaDenied(MediaDenial.NOT_IN_SCOPE)


@pytest.mark.parametrize(
    ("second", "expected"),
    [
        (ItemAccess.ALLOWED, VERIFIED),
        (ItemAccess.RESTRICTED, MediaDenied(MediaDenial.NOT_IN_SCOPE)),
        (ItemAccess.NOT_FOUND, MediaDenied(MediaDenial.UNRESOLVED)),
    ],
)
def test_an_item_matching_several_jellyfin_items_needs_every_one_to_allow_it(second, expected):
    target = TARGETS["path-family-film"]
    decisions = decide_media(
        linked("Casey"),
        [target],
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=catalog(
            [
                record(JELLYFIN_ITEMS[FAMILY_FILM]),
                record(JELLYFIN_ITEMS[FEATURE], MediaSources=[{"Id": "v9", "Path": FAMILY_PATH}]),
            ]
        ),
        items=Reader({FAMILY_FILM: ItemAccess.ALLOWED, FEATURE: second}),
        now=NOW,
    )
    assert decisions == {target.key: expected}


def test_a_jellyfin_item_of_another_server_is_unresolved():
    target = jellyfin_target("d" * 32, JELLYFIN_ITEMS[FAMILY_FILM], resolve)
    decisions = decide_media(
        linked("Casey"),
        [target],
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=catalog(),
        items=Reader(CASEY),
        now=NOW,
    )
    assert decisions == {target.key: MediaDenied(MediaDenial.UNRESOLVED)}


def test_the_owner_reaches_everything_without_any_read():
    reader = Reader(result=Unverified(UnverifiedReason.TIMEOUT))
    targets = [*TARGETS.values(), *JELLYFIN.values()]
    decisions = decide_media(
        OwnerSubject(),
        targets,
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=None,
        items=reader,
        now=NOW,
    )
    assert set(decisions.values()) == {MediaAllowed(MediaBasis.OWNER)}
    assert reader.requests == []
