"""MU-11: an outside file aliased by a restricted Jellyfin item of any type keeps its denial.

Jellyfin 12.0.0 lists local trailers, extras, audio, photos, and books as items
of their own types. Each case links a file in a library Casey's policy closes
to a file outside every library folder, which alone would be unmatched.
The catalog comes from the real adapter over a transport that filters `/Items`
the way Jellyfin does, so a type the listing leaves out is never indexed.
The fifth review's regression is the `Trailer` case; `web/test-results/
mu-11/fix-round-5/adaptations.md` records how its probe was adapted.
"""

from __future__ import annotations

import pytest

from scanner.integrations.jellyfin_library_folders import parse_library_folders
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDenial, MediaDenied
from scanner.security.media_access.libraries import LibraryRoots
from scanner.security.media_access.matching import JellyfinCatalogIndex
from scanner.security.media_access.service import decide_media
from tests.jellyfin_user_boundary import NOW, PRIMARY, scripted_transport
from tests.media_access_support import (
    FAMILY_FILM,
    FEATURE,
    MOVIES,
    jellyfin_items,
    jellyfin_user,
    path_target,
)
from tests.test_media_access_matrix import CASEY, Reader, linked

FILE_TYPES = [
    ("Trailer", "Video"),
    ("Video", "Video"),
    ("MusicVideo", "Video"),
    ("Audio", "Audio"),
    ("AudioBook", "Audio"),
    ("Photo", "Photo"),
    ("Book", "Book"),
    ("FutureKind", "Video"),
]


def _decide_outside_alias(tmp_path, alias_id, kind, media_type):
    library, outside = tmp_path / "Library", tmp_path / "Outside"
    library.mkdir()
    outside.mkdir()
    shared = outside / "Shared.mkv"
    shared.write_bytes(b"synthetic")
    (library / "Shared.mkv").symlink_to(shared)
    folders = [{"ItemId": MOVIES, "Locations": [str(library)]}]
    roots = LibraryRoots(parse_library_folders(folders, PRIMARY, NOW), str)
    alias = {
        "Id": alias_id,
        "Name": "Shared",
        "Type": kind,
        "MediaType": media_type,
        "Path": str(library / "Shared.mkv"),
        "MediaSources": [{"Id": "v0", "Path": str(library / "Shared.mkv")}],
    }
    transport = scripted_transport(jellyfin_items([alias]))
    with JellyfinUserAdapter(
        "http://jellyfin.test", "key", transport=transport, clock=lambda: NOW
    ) as adapter:
        listed = adapter.catalog(PRIMARY)
    target = path_target(str(shared))
    return decide_media(
        linked("Casey"),
        [target],
        user=jellyfin_user(),
        catalog=JellyfinCatalogIndex(listed, str),
        libraries=roots,
        items=Reader(CASEY),
        now=NOW,
    )[target.key]


@pytest.mark.parametrize(("kind", "media_type"), FILE_TYPES)
def test_an_outside_file_aliased_by_a_restricted_item_of_any_type_is_restricted(
    tmp_path, kind, media_type
):
    decision = _decide_outside_alias(tmp_path, FEATURE, kind, media_type)
    assert decision == MediaDenied(MediaDenial.NOT_IN_SCOPE)


@pytest.mark.parametrize(("kind", "media_type"), FILE_TYPES[:1] + FILE_TYPES[3:4])
def test_an_outside_file_aliased_by_an_item_jellyfin_opens_follows_that_item(
    tmp_path, kind, media_type
):
    decision = _decide_outside_alias(tmp_path, FAMILY_FILM, kind, media_type)
    assert decision == MediaAllowed(MediaBasis.JELLYFIN_VERIFIED)
