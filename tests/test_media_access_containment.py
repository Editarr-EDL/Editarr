"""The 2026-10-22 decision over synthetic files: a file inside a library folder must be a
Jellyfin item's file, and only a file proven outside every folder is unmatched.

Each test builds a private tree with one Jellyfin library folder, `Library`, and
a folder outside it, `Elsewhere`. Jellyfin restricts the feature for Casey and
lets Casey open the family film.
"""

import os

import pytest

from scanner.integrations.jellyfin_catalog import parse_catalog
from scanner.integrations.jellyfin_library_folders import parse_library_folders
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDenial, MediaDenied
from scanner.security.media_access import libraries as containment
from scanner.security.media_access.libraries import LibraryRoots
from scanner.security.media_access.matching import JellyfinCatalogIndex
from scanner.security.media_access.service import decide_media
from tests.jellyfin_user_boundary import NOW, PRIMARY
from tests.media_access_support import (
    FAMILY,
    FAMILY_FILM,
    FEATURE,
    MOVIES,
    jellyfin_user,
    listing,
    path_target,
)
from tests.test_media_access_matrix import CASEY, Reader, linked

ALLOWED = MediaAllowed(MediaBasis.JELLYFIN_VERIFIED)
UNMATCHED = MediaDenied(MediaDenial.UNMATCHED)
RESTRICTED = MediaDenied(MediaDenial.NOT_IN_SCOPE)
IN_LIBRARY = MediaDenied(MediaDenial.LIBRARY_UNMATCHED)
UNRESOLVED = MediaDenied(MediaDenial.UNRESOLVED)
UNPRIVILEGED = pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="needs an unprivileged POSIX user"
)


def _unmapped(path):
    return path


def _entry(item_id, path, resolve=_unmapped):
    """The listing record of a Jellyfin item whose only file is `path`."""

    return {"Id": item_id, "Name": "Synthetic", "Type": "Movie", "Path": resolve(str(path))}


def _roots(*folders, resolve=_unmapped):
    payload = [{"ItemId": FAMILY, "Locations": [str(folder) for folder in folders]}]
    return LibraryRoots(parse_library_folders(payload, PRIMARY, NOW), resolve)


def _decide(target, *, entries, roots):
    return decide_media(
        linked("Casey"),
        [target],
        user=jellyfin_user(),
        catalog=JellyfinCatalogIndex(parse_catalog([listing(entries)], PRIMARY, NOW), _unmapped),
        items=Reader(CASEY),
        libraries=roots,
        now=NOW,
    )[target.key]


@pytest.fixture
def tree(tmp_path):
    library = tmp_path / "Library"
    elsewhere = tmp_path / "Elsewhere"
    for folder in (library, elsewhere):
        folder.mkdir()
    for path in (library / "Restricted.mkv", library / "Family.mkv", library / "Camcorder.mkv"):
        path.write_bytes(b"synthetic media")
    (elsewhere / "Home Video.mkv").write_bytes(b"synthetic home video")
    entries = [
        _entry(FEATURE, library / "Restricted.mkv"),
        _entry(FAMILY_FILM, library / "Family.mkv"),
    ]
    return tmp_path, entries, _roots(library)


def test_only_files_outside_every_library_folder_are_unmatched(tree):
    root, entries, roots = tree
    decisions = {
        name: _decide(path_target(str(root / path)), entries=entries, roots=roots)
        for name, path in {
            "outside": "Elsewhere/Home Video.mkv",
            "inside-unindexed": "Library/Camcorder.mkv",
            "inside-allowed": "Library/Family.mkv",
            "inside-restricted": "Library/Restricted.mkv",
        }.items()
    }
    assert decisions == {
        "outside": UNMATCHED,
        "inside-unindexed": IN_LIBRARY,
        "inside-allowed": ALLOWED,
        "inside-restricted": RESTRICTED,
    }


@pytest.mark.parametrize("alias", ["symlinked-folder", "symlinked-file", "case", "mapped"])
def test_every_spelling_of_a_restricted_file_stays_restricted(tree, alias):
    root, _entries, roots = tree
    restricted = root / "Library" / "Restricted.mkv"
    reported, resolve = str(restricted), _unmapped
    if alias == "symlinked-folder":
        (root / "Shortcut").symlink_to(root / "Library", target_is_directory=True)
        reported = str(root / "Shortcut" / "Restricted.mkv")
    elif alias == "symlinked-file":
        (root / "Elsewhere" / "Link.mkv").symlink_to(restricted)
        restricted = root / "Elsewhere" / "Link.mkv"
    elif alias == "case":
        reported = str(root / "Library" / "restricted.mkv")
        if not os.path.exists(reported):
            pytest.skip("Case-sensitive filesystem")
    else:
        reported = "Z:\\Movies\\Restricted.mkv"

        def resolve(path):
            return path.replace("Z:\\Movies\\", f"{root / 'Library'}/")

    target = path_target(str(restricted))
    result = _decide(target, entries=[_entry(FEATURE, reported, resolve)], roots=roots)
    # A case-preserving host keeps the spelling, so a case-only alias stays a denied library file.
    assert result == (
        IN_LIBRARY if alias == "case" and os.path.normcase("A") == "A" else RESTRICTED
    )


@pytest.mark.parametrize("alias", ["symlinked-folder", "symlinked-file", "case", "library-link"])
def test_every_spelling_of_a_library_folder_contains_its_files(tree, alias):
    root, entries, roots = tree
    camcorder = root / "Library" / "Camcorder.mkv"
    if alias == "symlinked-folder":
        (root / "Elsewhere" / "Shortcut").symlink_to(root / "Library", target_is_directory=True)
        camcorder = root / "Elsewhere" / "Shortcut" / "Camcorder.mkv"
    elif alias == "symlinked-file":
        (root / "Elsewhere" / "Camcorder.mkv").symlink_to(camcorder)
        camcorder = root / "Elsewhere" / "Camcorder.mkv"
    elif alias == "case":
        camcorder = root / "LIBRARY" / "camcorder.mkv"
        if not camcorder.exists():
            pytest.skip("Case-sensitive filesystem")
    else:
        (root / "Mount").symlink_to(root / "Library", target_is_directory=True)
        roots = _roots(root / "Mount")
    assert _decide(path_target(str(camcorder)), entries=entries, roots=roots) == IN_LIBRARY


def test_a_hard_link_is_judged_by_where_it_lies(tree):
    """The decision covers files by folder. A hard link outside the libraries is
    such a file, so it stays unmatched rather than borrowing its twin's item."""

    root, entries, roots = tree
    os.link(root / "Library" / "Restricted.mkv", root / "Library" / "Copy.mkv")
    os.link(root / "Library" / "Restricted.mkv", root / "Elsewhere" / "Copy.mkv")
    inside = _decide(path_target(str(root / "Library/Copy.mkv")), entries=entries, roots=roots)
    outside = _decide(path_target(str(root / "Elsewhere/Copy.mkv")), entries=entries, roots=roots)
    assert (inside, outside) == (IN_LIBRARY, UNMATCHED)


def test_a_replaced_restricted_file_keeps_its_denial_under_a_retained_index(tree):
    root, entries, roots = tree
    restricted = root / "Library" / "Restricted.mkv"
    (root / "Library" / "Alias.mkv").symlink_to(restricted)
    index_entries = list(entries)
    (root / "Replacement.mkv").write_bytes(b"synthetic replacement")
    (root / "Replacement.mkv").replace(restricted)
    os.link(restricted, root / "Library" / "Linked.mkv")
    decisions = [
        _decide(path_target(str(root / "Library" / name)), entries=index_entries, roots=roots)
        for name in ("Restricted.mkv", "Alias.mkv", "Linked.mkv")
    ]
    assert decisions == [RESTRICTED, RESTRICTED, IN_LIBRARY]


@pytest.mark.parametrize(
    "case",
    [
        "missing-target",
        "relative-target",
        "unmapped-folder",
        "relative-folder",
        "file-ids-absent",
        "target-ids-absent",
        "folder-ids-absent",
    ],
)
def test_a_place_that_cannot_be_proven_is_never_outside(tree, monkeypatch, case):
    root, entries, roots = tree
    target = path_target(str(root / "Elsewhere" / "Home Video.mkv"))
    if case == "missing-target":
        (root / "Elsewhere" / "Home Video.mkv").unlink()
    elif case == "relative-target":
        monkeypatch.chdir(root)
        target = type(target)(
            key=target.key, source=target.source, paths=frozenset({"Elsewhere/Home Video.mkv"})
        )
    elif case == "unmapped-folder":
        roots = _roots(root / "Library", "/jellyfin-only/Movies")
    elif case == "relative-folder":
        monkeypatch.chdir(root)
        roots = _roots("Library")
    else:
        real_stat = os.stat

        def no_file_ids(path, *args, **kwargs):
            found = real_stat(path, *args, **kwargs)
            folder = os.fspath(path) == str(root / "Library")
            if (case == "target-ids-absent" and folder) or (
                case == "folder-ids-absent" and not folder
            ):
                return found
            return os.stat_result((found.st_mode, 0, *tuple(found)[2:]))

        monkeypatch.setattr(containment.os, "stat", no_file_ids)
    assert _decide(target, entries=entries, roots=roots) == UNRESOLVED


@UNPRIVILEGED
@pytest.mark.parametrize("locked", ["target-folder", "library-folder"])
def test_an_unreadable_place_is_never_outside(tree, locked):
    root, entries, _ = tree
    (root / "Library" / "Sub").mkdir()
    roots = _roots(root / "Library" / "Sub")
    folder = root / ("Elsewhere" if locked == "target-folder" else "Library")
    target = path_target(str(root / "Elsewhere" / "Home Video.mkv"))
    readable = _decide(target, entries=entries, roots=roots)
    folder.chmod(0)
    try:
        result = _decide(target, entries=entries, roots=roots)
    finally:
        folder.chmod(0o700)
    assert (readable, result) == (UNMATCHED, UNRESOLVED)


@UNPRIVILEGED
def test_an_unreadable_jellyfin_alias_cannot_hide_a_spelling_match(tree):
    """A target that spells a restricted item's path stays denied when that item's
    folder becomes unreadable, since its place is then unproven, and an allowed
    spelling stays allowed."""

    root, _entries, roots = tree
    locked = root / "Library" / "Locked"
    locked.mkdir()
    (locked / "Restricted.mkv").write_bytes(b"synthetic shared file")
    os.link(locked / "Restricted.mkv", root / "Library" / "Shared.mkv")
    (root / "Elsewhere" / "Alias.mkv").symlink_to(locked / "Restricted.mkv")
    targets = {
        "restricted-spelling": path_target(str(root / "Elsewhere" / "Alias.mkv")),
        "allowed-spelling": path_target(str(root / "Library" / "Shared.mkv")),
    }
    results = []
    for lock in (False, True):
        locked.chmod(0 if lock else 0o700)
        try:
            entries = [
                _entry(FAMILY_FILM, root / "Library" / "Shared.mkv"),
                _entry(FEATURE, locked / "Restricted.mkv"),
            ]
            results.append(
                {
                    name: _decide(target, entries=entries, roots=roots)
                    for name, target in targets.items()
                }
            )
        finally:
            locked.chmod(0o700)
    assert results == [
        {"restricted-spelling": RESTRICTED, "allowed-spelling": ALLOWED},
        {"restricted-spelling": UNRESOLVED, "allowed-spelling": ALLOWED},
    ]


def test_a_target_moved_into_a_library_during_placement_is_not_proven_outside(
    tmp_path, monkeypatch
):
    library, outside = tmp_path / "Library", tmp_path / "Outside"
    for folder in (library, outside):
        folder.mkdir()
        (folder / "File.mkv").write_bytes(b"synthetic media")
    target = path_target(str(outside / "File.mkv"))
    real_stat = os.stat
    moved = False

    def stat_and_move(path, *args, **kwargs):
        nonlocal moved
        found = real_stat(path, *args, **kwargs)
        if os.fspath(path) == str(tmp_path) and not moved:
            moved = True
            outside.rename(tmp_path / "Previous")
            outside.symlink_to(library, target_is_directory=True)
        return found

    with monkeypatch.context() as scoped:
        scoped.setattr(containment.os, "stat", stat_and_move)
        decision = _decide(target, entries=[], roots=_roots(library))
    assert moved and os.path.realpath(next(iter(target.paths))) == str(library / "File.mkv")
    assert decision == UNRESOLVED


def test_a_library_folder_missing_from_this_host_only_stops_proving_outside(tree):
    """The coordinator's 2026-10-22 ruling: Editarr often mounts only some libraries.

    A folder this host lacks cannot prove a file outside it, so the file is
    unresolved rather than unmatched. Files in a mounted library keep their
    decisions, closed ones included.
    """
    root, entries, _ = tree
    closed = root / "Closed"
    closed.mkdir()
    (closed / "Feature.mkv").write_bytes(b"synthetic media")
    payload = [
        {"ItemId": FAMILY, "Locations": [str(root / "Library")]},
        {"ItemId": MOVIES, "Locations": [str(closed)]},
        {"ItemId": "d4" * 16, "Locations": ["/jellyfin-only/Music", "Z:\\Music"]},
    ]
    roots = LibraryRoots(parse_library_folders(payload, PRIMARY, NOW), _unmapped)
    decisions = {
        name: _decide(path_target(str(root / path)), entries=entries, roots=roots)
        for name, path in {
            "outside": "Elsewhere/Home Video.mkv",
            "inside-unindexed": "Library/Camcorder.mkv",
            "inside-allowed": "Library/Family.mkv",
            "inside-restricted": "Library/Restricted.mkv",
            "closed-library": "Closed/Feature.mkv",
        }.items()
    }
    assert decisions == {
        "outside": UNRESOLVED,
        "inside-unindexed": IN_LIBRARY,
        "inside-allowed": ALLOWED,
        "inside-restricted": RESTRICTED,
        "closed-library": RESTRICTED,
    }
