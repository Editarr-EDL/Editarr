"""MU-11: local scopes, media keys, and the evaluator's use of media decisions."""

from __future__ import annotations

import pytest

from scanner.persistence.repository.media_access import StoredScope
from scanner.persistence.rows.db_policies import MediaSourceKind
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import Allowed, Denied
from scanner.security.access.evaluator import decide
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDenial, MediaDenied
from scanner.security.access.targets import MediaRef
from scanner.security.access.vocabulary import Role
from scanner.security.media_access.keys import (
    MalformedMediaKey,
    MediaKey,
    SourceRef,
)
from scanner.security.media_access.matching import jellyfin_target
from scanner.security.media_access.resolver import LocalScope, LocalSubject
from scanner.security.media_access.service import decide_media, parse_scope
from tests.jellyfin_user_boundary import NOW, PRIMARY
from tests.media_access_support import (
    FAMILY,
    FAMILY_FILM,
    FEATURE,
    JELLYFIN_ITEMS,
    MOVIES,
    TARGETS,
    jellyfin_user,
    libraries,
    resolve,
)
from tests.multi_user_access_support import build_access
from tests.test_media_access_matrix import (
    ALLOWED,
    JELLYFIN,
    RESTRICTED,
    UNMATCHED,
    UNRESOLVED,
)


def _local(all_sources=False, *sources, inherited=False):
    return LocalSubject(LocalScope(all_sources, frozenset(sources), inherited))


@pytest.mark.parametrize(
    ("subject", "allowed"),
    [
        (_local(True, inherited=True), "everything"),
        (_local(False, SourceRef(MediaSourceKind.JELLYFIN_LIBRARY, MOVIES)), {MOVIES}),
        (
            _local(
                False,
                SourceRef(MediaSourceKind.JELLYFIN_LIBRARY, MOVIES),
                SourceRef(MediaSourceKind.JELLYFIN_LIBRARY, FAMILY),
            ),
            {MOVIES, FAMILY},
        ),
        (_local(False, SourceRef(MediaSourceKind.JELLYFIN_LIBRARY, FAMILY)), {FAMILY}),
        (_local(False), set()),
    ],
)
def test_local_accounts_reach_the_sources_of_their_scope(subject, allowed):
    targets = list(JELLYFIN.values())
    decisions = decide_media(
        subject,
        targets,
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=None,
        items=None,
        now=NOW,
    )

    basis = MediaBasis.LOCAL_DEFAULT if subject.scope.inherited else MediaBasis.LOCAL_OVERRIDE
    for target in targets:
        expected = (
            MediaAllowed(basis)
            if allowed == "everything" or target.source.source_id in allowed
            else MediaDenied(MediaDenial.NOT_IN_SCOPE)
        )
        assert decisions[target.key] == expected, target.key


def test_an_item_without_a_known_library_is_unresolved_for_local_accounts():
    item = JELLYFIN_ITEMS[FAMILY_FILM].model_copy(update={"library_id": None})
    targets = [jellyfin_target(PRIMARY, item, resolve), *TARGETS.values()]
    decisions = decide_media(
        _local(True, inherited=True),
        targets,
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=None,
        items=None,
        now=NOW,
    )
    assert decisions == {target.key: UNRESOLVED for target in targets}


@pytest.mark.parametrize(
    "stored",
    [
        StoredScope(inherited=True, all_sources="yes", sources=frozenset()),
        StoredScope(inherited=True, all_sources=2, sources=frozenset()),
        StoredScope(
            inherited=False,
            all_sources=0,
            sources=frozenset({("plex", ""), ("manual_files", ""), ("jellyfin_library", "Movies")}),
        ),
        None,
    ],
    ids=["text-flag", "out-of-range-flag", "unknown-sources", "missing-default"],
)
def test_unreadable_stored_scopes_grant_nothing(stored):
    scope = parse_scope(stored)
    targets = list(JELLYFIN.values())
    decisions = decide_media(
        LocalSubject(scope),
        targets,
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=None,
        items=None,
        now=NOW,
    )
    assert set(decisions.values()) == {MediaDenied(MediaDenial.NOT_IN_SCOPE)}


def test_a_jellyfin_item_with_a_malformed_id_cannot_become_a_target():
    item = JELLYFIN_ITEMS[FAMILY_FILM].model_copy(update={"id": "../Items"})
    with pytest.raises(MalformedMediaKey):
        jellyfin_target(PRIMARY, item, resolve)


def test_jellyfin_targets_key_the_server_and_item():
    assert JELLYFIN[FEATURE].key == MediaKey(MediaSourceKind.JELLYFIN_LIBRARY, PRIMARY, FEATURE)
    assert JELLYFIN[FEATURE].source == SourceRef(MediaSourceKind.JELLYFIN_LIBRARY, MOVIES)


@pytest.mark.parametrize(
    ("role", "decision", "expected"),
    [
        (Role.USER, RESTRICTED, Denied(AccessErrorCode.NOT_FOUND)),
        (Role.ADMIN, UNMATCHED, Denied(AccessErrorCode.NOT_FOUND)),
        (Role.USER, ALLOWED, Allowed(Capability.MEDIA_BROWSE)),
        (Role.USER, MediaAllowed(MediaBasis.JELLYFIN_GRACE), Allowed(Capability.MEDIA_BROWSE)),
    ],
)
def test_the_evaluator_hides_media_the_decision_denies(role, decision, expected):
    assert decide(build_access(role), Capability.MEDIA_BROWSE, MediaRef(decision)) == expected
