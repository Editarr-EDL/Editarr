"""The media decision service for catalog, reviews, workers, and management endpoints.

Callers build `MediaTarget`s from source-server records, load the account's
`Subject` once per request or job run, and pass the linked server's evidence:
the user's verified snapshot, an index of one complete catalog listing, the
library-folder read, and an item-access reader (the MU-08 adapter, or a cache
over it). The evaluator then takes each decision through `MediaRef` or
`ResourceRef`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from scanner.integrations.jellyfin_user_snapshot import (
    ItemAccessSnapshot,
    JellyfinUserSnapshot,
    MediaPolicy,
    Unverified,
    UnverifiedReason,
)
from scanner.persistence.repository.media_access import StoredScope, read_scope
from scanner.persistence.rows.db_policies import MediaSourceKind
from scanner.security.access.identity import Account, ProviderIdentity, ProviderLinkState
from scanner.security.access.records import (
    MediaAllowed,
    MediaBasis,
    MediaDecision,
    MediaDenial,
    MediaDenied,
)
from scanner.security.access.vocabulary import Role

from .keys import MediaKey, SourceRef
from .libraries import LibraryRoots, Outside, OutsideMounted, place
from .matching import (
    Correspondence,
    InLibrary,
    JellyfinCatalogIndex,
    Matched,
    MediaTarget,
    Restricted,
    Unmatched,
    Unresolved,
)
from .resolver import (
    VERIFICATION_GRACE,
    LinkedSubject,
    LocalScope,
    LocalSubject,
    OwnerSubject,
    Subject,
    decide_linked,
    decide_local,
)

_BLOCKED = frozenset({ProviderLinkState.SERVER_REPLACED, ProviderLinkState.OUTAGE_EXPIRED})


class ItemAccessReader(Protocol):
    def item_access(
        self, identity: ProviderIdentity, item_ids: Sequence[str]
    ) -> ItemAccessSnapshot | Unverified: ...


def parse_scope(stored: StoredScope | None) -> LocalScope:
    """Parse a stored scope; an unreadable flag or source grants nothing."""

    if stored is None:
        return LocalScope(all_sources=False, sources=frozenset(), inherited=True)
    sources = set()
    for kind, source_id in stored.sources:
        try:
            sources.add(SourceRef(MediaSourceKind(kind), source_id))
        except ValueError:
            continue
    return LocalScope(
        all_sources=type(stored.all_sources) is int and stored.all_sources == 1,
        sources=frozenset(sources),
        inherited=stored.inherited,
    )


def load_subject(db: Session, account: Account) -> Subject:
    """Read what decides one account's media: nothing for the owner, else its scope or link."""

    if account.role is Role.OWNER:
        return OwnerSubject()
    if account.provider is None:
        return LocalSubject(parse_scope(read_scope(db, account.user_id)))
    return LinkedSubject(identity=account.provider, link_state=account.link_state)


@dataclass(frozen=True)
class LinkedEvidence:
    """The linked server's facts that every linked decision of one call rests on."""

    policy: MediaPolicy
    catalog: JellyfinCatalogIndex
    roots: LibraryRoots


def _usable(
    subject: LinkedSubject,
    user: JellyfinUserSnapshot | Unverified | None,
    catalog: JellyfinCatalogIndex | Unverified | None,
    libraries: LibraryRoots | Unverified | None,
    now: datetime,
) -> LinkedEvidence | MediaDenied:
    """Bind the evidence to the linked server and the 24-hour freshness rule, or deny all."""

    replaced = UnverifiedReason.SERVER_MISMATCH
    if any(
        isinstance(fact, Unverified) and fact.reason is replaced
        for fact in (user, catalog, libraries)
    ):
        return MediaDenied(MediaDenial.SERVER_REPLACED)
    server = subject.identity.server_id
    if not (
        isinstance(user, JellyfinUserSnapshot)
        and user.identity == subject.identity
        and isinstance(catalog, JellyfinCatalogIndex)
        and catalog.server_id == server
        and isinstance(libraries, LibraryRoots)
        and libraries.folders.server_id == server
    ):
        return MediaDenied(MediaDenial.UNRESOLVED)
    oldest = min(user.verified_at, catalog.read_at, libraries.folders.read_at)
    if now - oldest > VERIFICATION_GRACE:
        return MediaDenied(MediaDenial.VERIFICATION_EXPIRED)
    return LinkedEvidence(user.media, catalog, libraries)


def _correspond(
    subject: LinkedSubject, evidence: LinkedEvidence, target: MediaTarget
) -> Correspondence:
    if target.jellyfin_item is not None:
        # Item IDs are per server, so another server's item proves nothing here.
        if target.key.server != subject.identity.server_id:
            return Unresolved()
        return Matched((target.jellyfin_item,))
    placement = place(evidence.roots, target.paths)
    if isinstance(placement, Unresolved):
        return placement
    entries = evidence.catalog.match(target)
    if isinstance(placement, Outside | OutsideMounted):
        if entries:
            return Matched(entries)
        # A library folder missing from this host could hold the file.
        return Unmatched() if isinstance(placement, Outside) else Unresolved()
    if not all(evidence.policy.allows_library(library) for library in placement.library_ids):
        return Restricted()
    # Inside a library only the file's own Jellyfin item can vouch for it.
    return Matched(entries) if entries else InLibrary()


def decide_media(
    subject: Subject,
    targets: Sequence[MediaTarget],
    *,
    user: JellyfinUserSnapshot | Unverified | None,
    catalog: JellyfinCatalogIndex | Unverified | None,
    libraries: LibraryRoots | Unverified | None,
    items: ItemAccessReader | None,
    now: datetime,
) -> dict[MediaKey, MediaDecision]:
    """Decide every target for one subject, reading Jellyfin at most once.

    A linked decision needs all of the linked server's evidence: `user`, the
    user's verified snapshot; `catalog`, an index of one complete listing;
    `libraries`, the library-folder read. Each must be of the linked server and
    at most 24 hours old, or every target is denied. `items` None means item
    decisions are unavailable, which denies matched targets as unresolved.
    The owner and local accounts need none of it.
    """

    if isinstance(subject, OwnerSubject):
        return {target.key: MediaAllowed(MediaBasis.OWNER) for target in targets}
    if isinstance(subject, LocalSubject):
        return {target.key: decide_local(subject.scope, target) for target in targets}
    if subject.link_state in _BLOCKED:
        return {
            target.key: decide_linked(subject, Unresolved(), None, now=now)
            for target in targets
        }
    evidence = _usable(subject, user, catalog, libraries, now)
    if isinstance(evidence, MediaDenied):
        return {target.key: evidence for target in targets}
    matches = {target.key: _correspond(subject, evidence, target) for target in targets}
    item_ids = sorted(
        {
            entry.item_id
            for match in matches.values()
            if isinstance(match, Matched)
            for entry in match.entries
        }
    )
    reader = items.item_access(subject.identity, item_ids) if item_ids and items else None
    return {
        target.key: decide_linked(subject, matches[target.key], reader, now=now)
        for target in targets
    }
