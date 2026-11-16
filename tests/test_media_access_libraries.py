"""MU-11: library-folder evidence for path correspondence.

A file is unmatched only when it is proven to lie outside every library folder
of the linked server, and no linked decision stands without a complete,
current folder read of that server. The adapter cases drive the real MU-08 adapter over a scripted
transport shaped after Jellyfin 12.0.0's `/Library/VirtualFolders`.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from scanner.integrations.jellyfin_library_folders import LibraryFolders, parse_library_folders
from scanner.integrations.jellyfin_user_snapshot import InvalidPayload, Unverified, UnverifiedReason
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDenial, MediaDenied
from scanner.security.media_access.libraries import LibraryRoots
from scanner.security.media_access.service import decide_media
from tests.jellyfin_user_boundary import NOW, PRIMARY, REPLACEMENT, scripted_transport
from tests.media_access_support import (
    FAMILY,
    IN_LIBRARY,
    MOVIES,
    OUTSIDE,
    TARGETS,
    VIRTUAL_FOLDERS,
    catalog,
    jellyfin_user,
    libraries,
    path_target,
    resolve,
)
from tests.test_media_access_matrix import CASEY, Reader, linked

UNMATCHED = MediaDenied(MediaDenial.UNMATCHED)
UNRESOLVED = MediaDenied(MediaDenial.UNRESOLVED)
SAMPLE = ("path-unmatched", "path-in-library", "path-family-film")
RECORDED_FOLDERS = next(
    entry["body"]
    for entry in json.loads(
        (
            Path(__file__).parent / "fixtures/multi-user/jellyfin-catalog-v3/recording.json"
        ).read_text()
    )["responses"]
    if entry["path"] == "/Library/VirtualFolders"
)
"""Jellyfin 12.0.0's folders after a collection was made: five media libraries under
/media and the `boxsets` Collections library at /config/data/collections."""


def _decide(evidence, names=SAMPLE):
    reader = Reader(CASEY)
    decisions = decide_media(
        linked("Casey"),
        [TARGETS[name] for name in names],
        user=jellyfin_user(),
        catalog=catalog(),
        items=reader,
        libraries=evidence,
        now=NOW,
    )
    return {name: decisions[TARGETS[name].key] for name in names}, reader


def test_a_current_read_of_the_linked_server_proves_a_file_outside_every_library():
    for evidence in (libraries(), libraries(read_at=NOW - timedelta(hours=23))):
        decisions, _reader = _decide(evidence)
        assert decisions == {
            "path-unmatched": UNMATCHED,
            "path-in-library": MediaDenied(MediaDenial.LIBRARY_UNMATCHED),
            "path-family-film": MediaAllowed(MediaBasis.JELLYFIN_VERIFIED),
        }


@pytest.mark.parametrize(
    ("evidence", "denial"),
    [
        (libraries(read_at=NOW - timedelta(hours=24, seconds=1)), MediaDenial.VERIFICATION_EXPIRED),
        (libraries(server=REPLACEMENT), MediaDenial.UNRESOLVED),
        (Unverified(UnverifiedReason.TIMEOUT), MediaDenial.UNRESOLVED),
        (Unverified(UnverifiedReason.INVALID_RESPONSE), MediaDenial.UNRESOLVED),
        (None, MediaDenial.UNRESOLVED),
    ],
    ids=["stale", "another-server", "timeout", "invalid", "missing"],
)
def test_without_a_current_folder_read_every_linked_target_is_denied(evidence, denial):
    """The 2026-10-22 decision: if the linked server's library folders cannot be read, deny."""

    decisions, reader = _decide(evidence, names=tuple(TARGETS))
    assert set(decisions.values()) == {MediaDenied(denial)}
    assert reader.requests == []


def test_fresh_evidence_of_a_replaced_server_from_the_folder_read_blocks_everything():
    decisions, reader = _decide(Unverified(UnverifiedReason.SERVER_MISMATCH), names=tuple(TARGETS))
    assert set(decisions.values()) == {MediaDenied(MediaDenial.SERVER_REPLACED)}
    assert reader.requests == []


def test_a_server_without_libraries_leaves_every_unmatched_file_outside():
    empty = LibraryRoots(parse_library_folders([], PRIMARY, NOW), resolve)
    decisions, _reader = _decide(empty, names=tuple(OUTSIDE | IN_LIBRARY))
    assert set(decisions.values()) == {UNMATCHED}


def _recorded(host: Path, mounted: tuple[str, ...]) -> LibraryRoots:
    """The recorded folders, with only those under a `mounted` prefix present on `host`."""

    for library in RECORDED_FOLDERS:
        for folder in library["Locations"]:
            if folder.startswith(mounted):
                (host / folder.lstrip("/")).mkdir(parents=True)
    local = lambda path: str(host / path.lstrip("/")) if path.startswith(mounted) else path  # noqa: E731
    return LibraryRoots(parse_library_folders(RECORDED_FOLDERS, PRIMARY, NOW), local)


@pytest.mark.parametrize(
    ("mounted", "wanted"),
    [
        (("/media/",), UNMATCHED),
        (("/media/Movies", "/media/Family", "/media/Home", "/media/Shows"), UNRESOLVED),
    ],
    ids=["collections-unmounted", "songs-unmounted"],
)
def test_only_an_unmounted_media_library_stops_a_file_being_proven_outside(
    tmp_path, mounted, wanted
):
    """The collections library folder holds no media, so leaving /config unmapped hides nothing."""

    decisions, _reader = _decide(_recorded(tmp_path, mounted), names=tuple(OUTSIDE))
    assert set(decisions.values()) == {wanted}


@pytest.mark.parametrize(
    ("folder", "wanted"),
    [
        ("config/data/collections/Pair [boxset]", UNMATCHED),
        ("media/Movies/Film A (2001)", MediaDenied(MediaDenial.NOT_IN_SCOPE)),
    ],
    ids=["collections-folder", "movies-folder"],
)
def test_a_file_in_the_collections_folder_is_not_inside_a_library(tmp_path, folder, wanted):
    roots = _recorded(tmp_path, ("/media/", "/config/"))
    stray = tmp_path / folder / "Stray.mkv"
    stray.parent.mkdir(exist_ok=True)
    stray.touch()
    target = path_target(str(stray))
    decisions = decide_media(
        linked("Casey"),
        [target],
        user=jellyfin_user(),
        catalog=catalog(),
        items=Reader(CASEY),
        libraries=roots,
        now=NOW,
    )
    assert decisions == {target.key: wanted}


@pytest.mark.parametrize(
    "payload",
    [
        {"Items": VIRTUAL_FOLDERS},
        [*VIRTUAL_FOLDERS, "Movies"],
        [*VIRTUAL_FOLDERS, {"Locations": ["/jellyfin/extra"]}],
        [*VIRTUAL_FOLDERS, {"ItemId": "not-a-guid", "Locations": ["/jellyfin/extra"]}],
        [*VIRTUAL_FOLDERS, {"ItemId": "f" * 32}],
        [*VIRTUAL_FOLDERS, {"ItemId": "f" * 32, "Locations": "/jellyfin/extra"}],
        [*VIRTUAL_FOLDERS, {"ItemId": "f" * 32, "Locations": ["/jellyfin/extra", " "]}],
        [*VIRTUAL_FOLDERS, {"ItemId": "f" * 32, "Locations": [None]}],
        [*VIRTUAL_FOLDERS, {"ItemId": "f" * 32, "Locations": [], "CollectionType": 7}],
    ],
    ids=[
        "not-a-list",
        "not-an-object",
        "no-id",
        "bad-id",
        "no-folders",
        "folders-not-a-list",
        "blank-folder",
        "folder-not-text",
        "kind-not-text",
    ],
)
def test_one_malformed_library_rejects_the_whole_read(payload):
    with pytest.raises(InvalidPayload):
        parse_library_folders(payload, PRIMARY, NOW)


def test_library_folders_cannot_be_built_without_a_read():
    with pytest.raises(TypeError):
        LibraryFolders(PRIMARY, frozenset({"/jellyfin/movies"}), NOW)


def _adapter(answer, servers=("primary",)):
    return JellyfinUserAdapter(
        "http://jellyfin.test",
        "synthetic-key",
        transport=scripted_transport(answer, servers),
        clock=lambda: NOW,
    )


def _folders(request):
    assert request.url.path == "/Library/VirtualFolders"
    return httpx.Response(200, json=VIRTUAL_FOLDERS)


def test_the_adapter_reads_every_library_folder_of_the_linked_server():
    with _adapter(_folders) as adapter:
        read = adapter.library_folders(PRIMARY)
    assert (read.server_id, dict(read.libraries), read.read_at) == (
        PRIMARY,
        {MOVIES: frozenset({"/jellyfin/movies"}), FAMILY: frozenset({"/jellyfin/family"})},
        NOW,
    )


@pytest.mark.parametrize(
    ("server_id", "servers", "answer", "reason"),
    [
        (PRIMARY, ("primary", "replacement"), _folders, UnverifiedReason.SERVER_MISMATCH),
        (REPLACEMENT, ("primary",), _folders, UnverifiedReason.SERVER_MISMATCH),
        (PRIMARY, ("primary",), lambda _r: httpx.Response(500), UnverifiedReason.SERVICE_ERROR),
        (
            PRIMARY,
            ("primary",),
            lambda _r: httpx.Response(200, json={"Items": []}),
            UnverifiedReason.INVALID_RESPONSE,
        ),
    ],
    ids=["replaced-during-read", "another-server", "server-error", "malformed"],
)
def test_the_adapter_reports_any_incomplete_or_foreign_read_as_unverified(
    server_id, servers, answer, reason
):
    with _adapter(answer, servers) as adapter:
        assert adapter.library_folders(server_id) == Unverified(reason)
