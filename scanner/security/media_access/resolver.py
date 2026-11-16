"""The pure media decision for one account and one item.

The owner sees everything. A local account sees the sources of its scope: its
individual selection, or the live default when it has none. A linked account
sees what Jellyfin opens for it: a file inside a library folder must lie in
libraries its Jellyfin policy opens and be a Jellyfin item's file, and every
Jellyfin item the target corresponds to must allow it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from scanner.integrations.jellyfin_user_snapshot import (
    ItemAccess,
    ItemAccessSnapshot,
    Unverified,
    UnverifiedReason,
)
from scanner.security.access.identity import ProviderIdentity, ProviderLinkState
from scanner.security.access.link_state import VERIFICATION_GRACE as VERIFICATION_GRACE
from scanner.security.access.records import MediaAllowed, MediaBasis, MediaDecision, MediaDenial
from scanner.security.access.records import MediaDenied as Denied

from .keys import SourceRef
from .matching import Correspondence, InLibrary, Matched, MediaTarget, Restricted, Unmatched


@dataclass(frozen=True)
class LocalScope:
    """The sources a local account may reach; `inherited` means the live default applies."""

    all_sources: bool
    sources: frozenset[SourceRef]
    inherited: bool


@dataclass(frozen=True)
class OwnerSubject:
    pass


@dataclass(frozen=True)
class LocalSubject:
    scope: LocalScope


@dataclass(frozen=True)
class LinkedSubject:
    identity: ProviderIdentity
    link_state: ProviderLinkState


Subject = OwnerSubject | LocalSubject | LinkedSubject

ItemEvidence = ItemAccessSnapshot | Unverified | None
"""Jellyfin's per-item decisions for a linked account; None when nothing was checked."""


def decide_local(scope: LocalScope, target: MediaTarget) -> MediaDecision:
    if target.source is None:
        return Denied(MediaDenial.UNRESOLVED)
    if scope.all_sources or target.source in scope.sources:
        return MediaAllowed(
            MediaBasis.LOCAL_DEFAULT if scope.inherited else MediaBasis.LOCAL_OVERRIDE
        )
    return Denied(MediaDenial.NOT_IN_SCOPE)


def decide_linked(
    subject: LinkedSubject,
    correspondence: Correspondence,
    items: ItemEvidence,
    *,
    now: datetime,
) -> MediaDecision:
    """Decide from the link state, then correspondence, then Jellyfin's item decisions.

    `items` must cover every matched Jellyfin item. Evidence that the server was
    replaced blocks every target, unmatched ones included, until relinking.
    Evidence older than `VERIFICATION_GRACE` is expired even when the stored
    link state is current.
    """

    replaced = isinstance(items, Unverified) and items.reason is UnverifiedReason.SERVER_MISMATCH
    if subject.link_state is ProviderLinkState.SERVER_REPLACED or replaced:
        return Denied(MediaDenial.SERVER_REPLACED)
    if subject.link_state is ProviderLinkState.OUTAGE_EXPIRED:
        return Denied(MediaDenial.VERIFICATION_EXPIRED)
    if isinstance(correspondence, Unmatched):
        return Denied(MediaDenial.UNMATCHED)
    if isinstance(correspondence, InLibrary):
        return Denied(MediaDenial.LIBRARY_UNMATCHED)
    if isinstance(correspondence, Restricted):
        return Denied(MediaDenial.NOT_IN_SCOPE)
    if not isinstance(correspondence, Matched) or not isinstance(items, ItemAccessSnapshot):
        return Denied(MediaDenial.UNRESOLVED)
    if items.identity != subject.identity:
        return Denied(MediaDenial.UNRESOLVED)
    if now - items.verified_at > VERIFICATION_GRACE:
        return Denied(MediaDenial.VERIFICATION_EXPIRED)
    found = [items.decisions.get(entry.item_id) for entry in correspondence.entries]
    if ItemAccess.RESTRICTED in found:
        # Jellyfin's per-item route does not say whether a library or a parental limit applied.
        return Denied(MediaDenial.NOT_IN_SCOPE)
    if not found or any(access is not ItemAccess.ALLOWED for access in found):
        return Denied(MediaDenial.UNRESOLVED)
    grace = subject.link_state is ProviderLinkState.OUTAGE_GRACE
    return MediaAllowed(MediaBasis.JELLYFIN_GRACE if grace else MediaBasis.JELLYFIN_VERIFIED)
