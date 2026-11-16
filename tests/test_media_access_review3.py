"""The third MU-11 review's probes, adapted to the typed linked-server evidence.

Each test keeps the reviewer's scenario and expectation. Adaptations, listed in
`web/test-results/mu-11/fix-round-3/adaptations.md`, only supply the new
required `user` snapshot and build catalogs the one way the code now allows,
from a complete listing.
"""

import os
from datetime import timedelta

import pytest

from scanner.integrations.jellyfin_catalog import parse_catalog
from scanner.integrations.jellyfin_library_folders import parse_library_folders
from scanner.integrations.jellyfin_user_snapshot import InvalidPayload, Unverified, UnverifiedReason
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDenial, MediaDenied
from scanner.security.media_access import libraries as containment
from scanner.security.media_access.libraries import LibraryRoots
from scanner.security.media_access.matching import jellyfin_target
from scanner.security.media_access.resolver import OwnerSubject
from scanner.security.media_access.service import decide_media
from tests.jellyfin_user_boundary import NOW, PRIMARY, REPLACEMENT
from tests.media_access_support import (
    FAMILY,
    FAMILY_FILM,
    JELLYFIN_ITEMS,
    MATURE,
    TARGETS,
    catalog,
    jellyfin_user,
    libraries,
    listing,
    path_target,
    record,
    resolve,
)
from tests.test_media_access_matrix import CASEY, Reader, linked


def covering(folder):
    payload = [{"ItemId": FAMILY, "Locations": [str(folder)]}]
    return LibraryRoots(parse_library_folders(payload, PRIMARY, NOW), lambda path: path)


def _decide(subject, targets, *, catalog, libraries, items=None):
    return decide_media(
        subject,
        targets,
        user=jellyfin_user(),
        catalog=catalog,
        items=items or Reader(CASEY),
        libraries=libraries,
        now=NOW,
    )


@pytest.mark.parametrize(
    "folders",
    [
        Unverified(UnverifiedReason.TIMEOUT),
        Unverified(UnverifiedReason.INVALID_RESPONSE),
        None,
        libraries(server=REPLACEMENT),
        libraries(read_at=NOW - timedelta(hours=24, seconds=1)),
    ],
    ids=["timeout", "invalid", "missing", "foreign", "expired"],
)
def test_failed_folder_evidence_denies_even_a_positive_item(folders):
    item = JELLYFIN_ITEMS[FAMILY_FILM]
    targets = [
        jellyfin_target(PRIMARY, item, resolve),
        path_target(resolve(item.path)),
    ]
    decisions = _decide(linked("Casey"), targets, catalog=catalog(), libraries=folders)
    assert all(isinstance(decision, MediaDenied) for decision in decisions.values())


def test_owner_keeps_access_despite_a_failed_folder_read():
    target = TARGETS["path-family-film"]
    assert decide_media(
        OwnerSubject(),
        [target],
        user=None,
        catalog=None,
        items=None,
        libraries=Unverified(UnverifiedReason.TIMEOUT),
        now=NOW,
    ) == {target.key: MediaAllowed(MediaBasis.OWNER)}


def _truncated(records):
    """The first page of a listing whose total names every fixture item."""

    every = [record(item) for item in JELLYFIN_ITEMS.values()]
    return [{**listing(records)[0], "TotalRecordCount": len(every)}]


def test_partial_catalog_cannot_drop_a_restriction_behind_an_allowed_match():
    target = path_target(resolve(JELLYFIN_ITEMS[MATURE].path))
    subject = linked("Casey")
    complete = _decide(subject, [target], catalog=catalog(), libraries=libraries())[target.key]
    with pytest.raises(InvalidPayload):
        parse_catalog([_truncated([record(JELLYFIN_ITEMS[FAMILY_FILM])])], PRIMARY, NOW)
    without_mature = catalog(
        [record(item) for item in JELLYFIN_ITEMS.values() if item.id != MATURE]
    )
    after = [
        _decide(subject, [target], catalog=index, libraries=libraries())[target.key]
        for index in (None, without_mature)
    ]
    assert isinstance(complete, MediaDenied)
    assert all(isinstance(decision, MediaDenied) for decision in after)


def test_partial_catalog_cannot_turn_an_outside_known_denial_into_unmatched():
    target = TARGETS["path-unmatched"]
    home_video = {"Id": "v9", "Path": "/jellyfin/../srv/other/Synthetic Home Video.mkv"}
    known = catalog(
        [
            record(item, **({"MediaSources": [home_video]} if item.id == MATURE else {}))
            for item in JELLYFIN_ITEMS.values()
        ]
    )
    complete = _decide(linked("Casey"), [target], catalog=known, libraries=libraries())
    with pytest.raises(InvalidPayload):
        parse_catalog([_truncated([])], PRIMARY, NOW)
    reader = Reader(result=Unverified(UnverifiedReason.TIMEOUT))
    after = _decide(linked("Casey"), [target], catalog=None, libraries=libraries(), items=reader)
    assert complete[target.key] == MediaDenied(MediaDenial.NOT_IN_SCOPE)
    assert after[target.key] == MediaDenied(MediaDenial.UNRESOLVED)


@pytest.mark.parametrize("link_kind", ["file", "directory"])
def test_a_path_beneath_a_library_cannot_escape_placement_through_a_symlink(tmp_path, link_kind):
    library = tmp_path / "Library"
    elsewhere = tmp_path / "Elsewhere"
    library.mkdir()
    elsewhere.mkdir()
    original = library / "Home.mkv"
    original.write_bytes(b"synthetic media")
    roots = covering(library)
    target = path_target(str(original))
    subject = linked("Casey")
    before = _decide(subject, [target], catalog=catalog([]), libraries=roots)[target.key]
    original.rename(elsewhere / "Home.mkv")
    if link_kind == "file":
        original.symlink_to(elsewhere / "Home.mkv")
    else:
        (library / "Linked").symlink_to(elsewhere, target_is_directory=True)
        target = path_target(str(library / "Linked" / "Home.mkv"))
    subject = linked("Casey")
    after = _decide(subject, [target], catalog=catalog([]), libraries=roots)[target.key]
    assert os.path.commonpath([next(iter(target.paths)), str(library)]) == str(library)
    assert before == after == MediaDenied(MediaDenial.LIBRARY_UNMATCHED)


def test_library_replacement_during_placement_does_not_prove_outside(tmp_path, monkeypatch):
    library = tmp_path / "Library"
    library.mkdir()
    filename = library / "Unindexed.mkv"
    filename.write_bytes(b"synthetic original")
    target = path_target(str(filename))
    roots = covering(library)
    real_stat = os.stat
    replaced = False

    def stat_and_replace(path, *args, **kwargs):
        nonlocal replaced
        found = real_stat(path, *args, **kwargs)
        if os.fspath(path) == str(library) and not replaced:
            replaced = True
            library.rename(tmp_path / "Previous")
            library.mkdir()
            filename.write_bytes(b"synthetic replacement")
        return found

    with monkeypatch.context() as scoped:
        scoped.setattr(containment.os, "stat", stat_and_replace)
        decision = _decide(
            linked("Casey"),
            [target],
            catalog=catalog([]),
            libraries=roots,
        )[target.key]
    assert replaced and filename.read_bytes() == b"synthetic replacement"
    assert decision == MediaDenied(MediaDenial.UNRESOLVED)
