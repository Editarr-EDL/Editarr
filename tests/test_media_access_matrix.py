"""MU-11: the cross-source media decision matrix.

The first tests drive the real MU-08 adapter against the controlled Jellyfin
boundary (fixture `editarr-jellyfin-users-1`, synthetic and shaped after
Jellyfin 12.0.0). They are deterministic and are not live-server evidence. The
rest pin each decision rule with a scripted item-access reader.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from types import MappingProxyType

import pytest

from scanner.integrations.jellyfin_user_snapshot import (
    ItemAccess,
    ItemAccessSnapshot,
    Unverified,
    UnverifiedReason,
)
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security.access.identity import ProviderLinkState
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDenial, MediaDenied
from scanner.security.media_access.matching import jellyfin_target
from scanner.security.media_access.resolver import (
    LinkedSubject,
)
from scanner.security.media_access.service import decide_media
from tests.jellyfin_user_boundary import IDS, NOW, PRIMARY, JellyfinBoundary, identity
from tests.media_access_support import (
    EPISODE,
    FAMILY_FILM,
    FEATURE,
    IN_LIBRARY,
    JELLYFIN_ITEMS,
    LATE_SHOW,
    MATURE,
    MOVIES_LIBRARY,
    OUTSIDE,
    SERIES,
    TARGETS,
    catalog,
    jellyfin_user,
    libraries,
    record,
    resolve,
)

ALLOWED = MediaAllowed(MediaBasis.JELLYFIN_VERIFIED)
RESTRICTED = MediaDenied(MediaDenial.NOT_IN_SCOPE)
UNMATCHED = MediaDenied(MediaDenial.UNMATCHED)
UNRESOLVED = MediaDenied(MediaDenial.UNRESOLVED)
LIBRARY_UNMATCHED = MediaDenied(MediaDenial.LIBRARY_UNMATCHED)
JELLYFIN = {
    item_id: jellyfin_target(PRIMARY, item, resolve) for item_id, item in JELLYFIN_ITEMS.items()
}


def linked(name, *, state=ProviderLinkState.VERIFIED):
    return LinkedSubject(identity(name), state)


def _named(decisions, targets):
    return {name: decisions[target.key] for name, target in targets.items()}


@pytest.fixture
def adapter():
    passwords = {user: secrets.token_urlsafe(18) for user in IDS.values()}
    with JellyfinBoundary(secrets.token_urlsafe(24), passwords) as server:
        with JellyfinUserAdapter(server.url, server.api_key, clock=lambda: NOW) as adapter:
            yield adapter


_BOUNDARY_TARGETS = {
    **TARGETS,
    "jellyfin-feature": JELLYFIN[FEATURE],
    "jellyfin-family-film": JELLYFIN[FAMILY_FILM],
    "jellyfin-late-show": JELLYFIN[LATE_SHOW],
    "jellyfin-mature": JELLYFIN[MATURE],
}


def test_a_restricted_user_cannot_reach_restricted_media_through_any_source(adapter):
    """Only the family film opens for Casey: library, tag, and rating limits hold through
    an alternate version and through path correspondence, and an unmatched path stays shut."""

    decisions = decide_media(
        linked("Casey"),
        list(_BOUNDARY_TARGETS.values()),
        libraries=libraries(),
        user=adapter.verify_user(identity("Casey")),
        catalog=catalog(),
        items=adapter,
        now=NOW,
    )

    assert _named(decisions, _BOUNDARY_TARGETS) == {
        "path-family-film": ALLOWED,
        "path-late-show": RESTRICTED,
        "path-alternate-version": RESTRICTED,
        "path-in-library": LIBRARY_UNMATCHED,
        "path-unmatched": UNMATCHED,
        "jellyfin-feature": RESTRICTED,
        "jellyfin-family-film": ALLOWED,
        "jellyfin-late-show": RESTRICTED,
        "jellyfin-mature": RESTRICTED,
    }


def test_an_unrestricted_user_reaches_matched_media_but_never_unmatched_media(adapter):
    decisions = decide_media(
        linked("Blake"),
        list(_BOUNDARY_TARGETS.values()),
        libraries=libraries(),
        user=adapter.verify_user(identity("Blake")),
        catalog=catalog(),
        items=adapter,
        now=NOW,
    )

    assert _named(decisions, _BOUNDARY_TARGETS) == {
        name: UNMATCHED if name in OUTSIDE else LIBRARY_UNMATCHED if name in IN_LIBRARY else ALLOWED
        for name in _BOUNDARY_TARGETS
    }


class Reader:
    """A scripted item-access reader that records each request."""

    def __init__(self, decisions=None, *, result=None, name="Casey", verified_at=NOW):
        self.decisions = decisions or {}
        self.result = result
        self.name = name
        self.verified_at = verified_at
        self.requests = []

    def item_access(self, user, item_ids):
        self.requests.append((user, tuple(item_ids)))
        if self.result is not None:
            return self.result
        found = {item: self.decisions.get(item, ItemAccess.NOT_FOUND) for item in item_ids}
        return ItemAccessSnapshot(identity(self.name), MappingProxyType(found), self.verified_at)


CASEY = {
    FEATURE: ItemAccess.RESTRICTED,
    FAMILY_FILM: ItemAccess.ALLOWED,
    LATE_SHOW: ItemAccess.RESTRICTED,
    MATURE: ItemAccess.RESTRICTED,
    SERIES: ItemAccess.ALLOWED,
    EPISODE: ItemAccess.RESTRICTED,
}


def test_one_read_covers_every_target():
    reader = Reader(CASEY)
    decisions = decide_media(
        linked("Casey"),
        list(TARGETS.values()),
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=catalog(),
        items=reader,
        now=NOW,
    )

    assert decisions[TARGETS["path-family-film"].key] == ALLOWED
    assert decisions[TARGETS["path-unmatched"].key] == UNMATCHED
    assert len(reader.requests) == 1
    assert set(reader.requests[0][1]) == {FAMILY_FILM, LATE_SHOW}


_HOME_VIDEO = {"Id": "v9", "Path": "/jellyfin/../srv/other/Synthetic Home Video.mkv"}


def test_an_unmatched_path_follows_the_restricted_jellyfin_item_it_later_matches():
    target = TARGETS["path-unmatched"]
    before = decide_media(
        linked("Casey"),
        [target],
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=catalog(),
        items=Reader(CASEY),
        now=NOW,
    )
    later = catalog(
        [
            record(item, **({"MediaSources": [_HOME_VIDEO]} if item.id == MATURE else {}))
            for item in JELLYFIN_ITEMS.values()
        ]
    )
    after = decide_media(
        linked("Casey"),
        [target],
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=later,
        items=Reader(CASEY),
        now=NOW,
    )

    assert before == {target.key: UNMATCHED}
    assert after == {target.key: RESTRICTED}


@pytest.mark.parametrize(
    ("state", "verified_at", "expected_allowed"),
    [
        (ProviderLinkState.VERIFIED, NOW - timedelta(hours=23), ALLOWED),
        (
            ProviderLinkState.OUTAGE_GRACE,
            NOW - timedelta(hours=23),
            MediaAllowed(MediaBasis.JELLYFIN_GRACE),
        ),
        (
            ProviderLinkState.OUTAGE_GRACE,
            NOW - timedelta(hours=24, seconds=1),
            MediaDenied(MediaDenial.VERIFICATION_EXPIRED),
        ),
        (
            ProviderLinkState.VERIFIED,
            NOW - timedelta(hours=25),
            MediaDenied(MediaDenial.VERIFICATION_EXPIRED),
        ),
    ],
)
def test_last_verified_decisions_last_24_hours(state, verified_at, expected_allowed):
    target = TARGETS["path-family-film"]
    decisions = decide_media(
        linked("Casey", state=state),
        [target],
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=catalog(),
        items=Reader(CASEY, verified_at=verified_at),
        now=NOW,
    )
    assert decisions == {target.key: expected_allowed}


@pytest.mark.parametrize(
    ("state", "denial"),
    [
        (ProviderLinkState.SERVER_REPLACED, MediaDenial.SERVER_REPLACED),
        (ProviderLinkState.OUTAGE_EXPIRED, MediaDenial.VERIFICATION_EXPIRED),
    ],
)
def test_a_replaced_server_or_expired_verification_blocks_everything_without_reading(state, denial):
    reader = Reader(CASEY)
    decisions = decide_media(
        linked("Casey", state=state),
        list(TARGETS.values()),
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=catalog(),
        items=reader,
        now=NOW,
    )
    assert set(decisions.values()) == {MediaDenied(denial)}
    assert reader.requests == []


@pytest.mark.parametrize(
    ("result", "matched", "unmatched", "in_library", "closed"),
    [
        (
            Unverified(UnverifiedReason.TIMEOUT),
            UNRESOLVED,
            UNMATCHED,
            LIBRARY_UNMATCHED,
            RESTRICTED,
        ),
        (
            Unverified(UnverifiedReason.INVALID_RESPONSE),
            UNRESOLVED,
            UNMATCHED,
            LIBRARY_UNMATCHED,
            RESTRICTED,
        ),
        (
            Unverified(UnverifiedReason.SERVER_MISMATCH),
            *[MediaDenied(MediaDenial.SERVER_REPLACED)] * 4,
        ),
    ],
    ids=["timeout", "invalid-response", "server-replaced"],
)
def test_an_unverified_item_read_denies_matched_media_and_a_replaced_server_denies_all(
    result, matched, unmatched, in_library, closed
):
    """Library placement needs no item read, so a file in a closed library stays restricted."""

    decisions = decide_media(
        linked("Casey"),
        list(TARGETS.values()),
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=catalog(),
        items=Reader(result=result),
        now=NOW,
    )
    expected = {name: matched for name in TARGETS}
    expected |= {name: unmatched for name in OUTSIDE} | {name: closed for name in MOVIES_LIBRARY}
    expected |= {name: in_library for name in IN_LIBRARY - MOVIES_LIBRARY}
    assert _named(decisions, TARGETS) == expected


def test_an_unavailable_catalog_or_reader_leaves_every_target_unresolved():
    no_catalog = decide_media(
        linked("Casey"),
        list(TARGETS.values()),
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=None,
        items=Reader(CASEY),
        now=NOW,
    )
    no_reader = decide_media(
        linked("Casey"),
        [JELLYFIN[FAMILY_FILM]],
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=catalog(),
        items=None,
        now=NOW,
    )

    assert set(no_catalog.values()) == {UNRESOLVED}
    assert no_reader == {JELLYFIN[FAMILY_FILM].key: UNRESOLVED}


@pytest.mark.parametrize(
    "reader",
    [
        Reader({FAMILY_FILM: ItemAccess.ALLOWED}, name="Blake"),
        Reader({}),
    ],
    ids=["another-users-decisions", "item-gone-since-indexing"],
)
def test_evidence_that_does_not_cover_the_target_is_unresolved(reader):
    target = TARGETS["path-family-film"]
    decisions = decide_media(
        linked("Casey"),
        [target],
        libraries=libraries(),
        user=jellyfin_user(),
        catalog=catalog(),
        items=reader,
        now=NOW,
    )
    assert decisions == {target.key: UNRESOLVED}
