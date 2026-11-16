"""MU-11: collections and stacked files never hide a restricted item's files from the catalog.

Measured live on Jellyfin 12.0.0 (web/test-results/mu-11/fix-round-6): unless a
listing sets `CollapseBoxSetItems=false`, a server-key `/Items` read replaces the
movies and series of a collection with its `BoxSet`, and a stacked movie lists
only its first part; the later parts come from `/Videos/{id}/AdditionalParts`.
The catalog comes from the real adapter over a transport that behaves that way.
"""

from __future__ import annotations

import pytest

from scanner.integrations.jellyfin_catalog import JellyfinCatalog, parse_catalog
from scanner.integrations.jellyfin_library_folders import parse_library_folders
from scanner.integrations.jellyfin_user_snapshot import (
    InvalidPayload,
    Unverified,
    UnverifiedReason,
)
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDenial, MediaDenied
from scanner.security.media_access.libraries import LibraryRoots
from scanner.security.media_access.matching import JellyfinCatalogIndex
from scanner.security.media_access.service import decide_media
from tests.jellyfin_user_boundary import NOW, PRIMARY, scripted_transport
from tests.media_access_support import (
    EPISODE,
    FAMILY_FILM,
    FEATURE,
    JELLYFIN_ITEMS,
    MOVIES,
    ROOT,
    SERIES,
    jellyfin_items,
    jellyfin_user,
    libraries,
    listing,
    path_target,
    record,
    resolve,
)
from tests.test_media_access_matrix import CASEY, Reader, linked

BOXSET = {
    "Id": "f6000000000000000000000000000001",
    "Name": "Synthetic Collection",
    "Type": "BoxSet",
    "MediaType": "Unknown",
    "Path": "/config/data/collections/Synthetic Collection [boxset]",
}
DENIED = MediaDenied(MediaDenial.NOT_IN_SCOPE)


def _catalog(records, **behavior):
    transport = scripted_transport(jellyfin_items(records, **behavior))
    with JellyfinUserAdapter(
        "http://jellyfin.test", "key", transport=transport, clock=lambda: NOW
    ) as adapter:
        return adapter.catalog(PRIMARY)


def _decide(listed, target, roots, resolver):
    return decide_media(
        linked("Casey"),
        [target],
        user=jellyfin_user(),
        catalog=JellyfinCatalogIndex(listed, resolver),
        libraries=roots,
        items=Reader(CASEY),
        now=NOW,
    )[target.key]


@pytest.mark.parametrize("members", [[FEATURE], [FEATURE, FAMILY_FILM]], ids=["alone", "with-open"])
def test_an_outside_copy_of_a_collected_restricted_movie_keeps_its_denial(members):
    home_video = {"Id": "v9", "Path": "/jellyfin/../srv/other/Synthetic Home Video.mkv"}
    records = [
        record(item, **({"MediaSources": [home_video]} if item.id == FEATURE else {}))
        for item in JELLYFIN_ITEMS.values()
    ]
    outside = path_target(f"{ROOT}/srv/other/Synthetic Home Video.mkv")
    listed = _catalog(records, boxsets={BOXSET["Id"]: (BOXSET, members)})
    assert isinstance(listed, JellyfinCatalog)
    assert {FEATURE, FAMILY_FILM} <= {item.id for item in listed.items}
    assert _decide(listed, outside, libraries(), resolve) == DENIED


def test_a_collected_series_still_gives_its_episodes_their_context():
    records = [record(item) for item in JELLYFIN_ITEMS.values()]
    listed = _catalog(records, boxsets={BOXSET["Id"]: (BOXSET, [SERIES])})
    assert isinstance(listed, JellyfinCatalog)
    episode = next(item for item in listed.items if item.id == EPISODE)
    assert episode.media_identity.series_identifiers


def _stacked(tmp_path, part_count=2, parts=None):
    library, outside = tmp_path / "Library", tmp_path / "Outside"
    (library / "Feature").mkdir(parents=True)
    outside.mkdir()
    shared = outside / "Shared Part.mkv"
    shared.write_bytes(b"synthetic")
    (library / "Feature/Feature - cd1.mkv").write_bytes(b"synthetic")
    (library / "Feature/Feature - cd2.mkv").symlink_to(shared)
    first = str(library / "Feature/Feature - cd1.mkv")
    feature = record(
        JELLYFIN_ITEMS[FEATURE],
        Path=first,
        MediaSources=[{"Id": "v0", "Path": first}],
        PartCount=part_count,
    )
    second = {
        "Id": "f7000000000000000000000000000001",
        "Type": "Video",
        "MediaType": "Video",
        "Path": str(library / "Feature/Feature - cd2.mkv"),
    }
    roots = LibraryRoots(
        parse_library_folders([{"ItemId": MOVIES, "Locations": [str(library)]}], PRIMARY, NOW), str
    )
    return feature, {FEATURE: [second] if parts is None else parts}, shared, roots


def test_an_outside_file_aliased_by_a_later_part_of_a_restricted_movie_keeps_its_denial(tmp_path):
    feature, parts, shared, roots = _stacked(tmp_path)
    listed = _catalog([feature], parts=parts)
    assert isinstance(listed, JellyfinCatalog)
    assert _decide(listed, path_target(str(shared)), roots, str) == DENIED


def test_a_later_part_of_an_open_movie_follows_that_movie(tmp_path):
    feature, parts, shared, roots = _stacked(tmp_path)
    listed = _catalog([{**feature, "Id": FAMILY_FILM}], parts={FAMILY_FILM: parts[FEATURE]})
    allowed = MediaAllowed(MediaBasis.JELLYFIN_VERIFIED)
    assert _decide(listed, path_target(str(shared)), roots, str) == allowed


@pytest.mark.parametrize(
    ("part_count", "parts"),
    [(2, []), (3, None), ("2", None), (2, [{"Id": "f7000000000000000000000000000001"}])],
    ids=["parts-missing", "fewer-parts-than-counted", "count-not-a-number", "part-without-path"],
)
def test_a_stacked_movie_whose_parts_disagree_with_its_count_rejects_the_catalog(
    tmp_path, part_count, parts
):
    feature, served, _shared, _roots = _stacked(tmp_path, part_count, parts)
    assert _catalog([feature], parts=served) == Unverified(UnverifiedReason.INVALID_RESPONSE)


def test_parsing_a_stacked_movie_without_its_parts_is_refused(tmp_path):
    feature, _parts, _shared, _roots = _stacked(tmp_path)
    with pytest.raises(InvalidPayload):
        parse_catalog([listing([feature])], PRIMARY, NOW)


def test_parts_for_a_movie_that_is_not_stacked_are_refused(tmp_path):
    feature, parts, _shared, _roots = _stacked(tmp_path, part_count=1)
    with pytest.raises(InvalidPayload):
        parse_catalog([listing([feature])], PRIMARY, NOW, parts)
