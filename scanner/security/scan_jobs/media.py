"""Media decisions for scans: the file each scan reads, decided for one account.

A scan's target is rebuilt from `ScanMedia`, which submission code writes from
the source server's own record. Jellyfin items need a server GUID in their key:
a linked account uses its linked server, and the owner and local accounts,
whose decisions never compare server keys, use `UNKNOWN_SERVER`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from scanner.integrations.jellyfin_user_snapshot import (
    InvalidPayload,
    ItemAccessSnapshot,
    Unverified,
    UnverifiedReason,
    canonical_guid,
)
from scanner.persistence.repository.repository_paths import resolve_local_path
from scanner.persistence.repository.scan_jobs import ScanMedia
from scanner.persistence.rows.db_policies import MediaSourceKind
from scanner.security.access.identity import Account, ProviderIdentity
from scanner.security.access.records import (
    MediaAllowed,
    MediaBasis,
    MediaDecision,
    MediaDenial,
    MediaDenied,
)
from scanner.security.access.vocabulary import Role
from scanner.security.jellyfin_accounts.evidence import EvidenceCache, MediaEvidence, evidence_cache
from scanner.security.media_access.keys import MalformedMediaKey, MediaKey, SourceRef
from scanner.security.media_access.matching import (
    CatalogEntry,
    MediaTarget,
    spellings,
)
from scanner.security.media_access.service import ItemAccessReader, decide_media, load_subject

UNKNOWN_SERVER = "0" * 32
"""The Jellyfin server key for accounts whose decisions never read it."""

@dataclass
class CapturedItems:
    """Materialize the item's adapter answer, then replay it without network access."""

    reader: ItemAccessReader | None
    identity: ProviderIdentity | None = None
    answer: ItemAccessSnapshot | Unverified = Unverified(UnverifiedReason.UNREACHABLE)

    def item_access(
        self, identity: ProviderIdentity, item_ids: Sequence[str]
    ) -> ItemAccessSnapshot | Unverified:
        if self.reader is not None:
            self.identity = identity
            self.answer = self.reader.item_access(identity, item_ids)
        if identity != self.identity or (
            isinstance(self.answer, ItemAccessSnapshot)
            and not set(item_ids).issubset(self.answer.decisions)
        ):
            return Unverified(UnverifiedReason.UNREACHABLE)
        return self.answer


@dataclass(frozen=True)
class ScanEvidence:
    """Closed catalog evidence and a materialized item answer for locked decisions."""

    value: MediaEvidence
    items: CapturedItems


def gather_evidence(db: Session, account: Account, media: ScanMedia, now: datetime) -> ScanEvidence:
    """Fetch and close every provider read before the job takes the SQLite write lock."""
    with evidence_cache.evidence(db, account, _resolver(db), now) as value:
        items = CapturedItems(value.items)
        evidence = ScanEvidence(value, items)
        decide_scans(db, account, [media], now, evidence=evidence)
    items.reader = None
    value.items = None
    return evidence


def scan_target(media: ScanMedia, jellyfin_server: str) -> MediaTarget | None:
    """The target a scan reads, or None when its stored facts cannot name one item."""

    paths = frozenset({media.resolved_path})
    try:
        item_id = canonical_guid(media.item_key)
        library = _library(media.library_id)
        entry = CatalogEntry(item_id, library, frozenset().union(*map(spellings, paths)))
        return MediaTarget(
            MediaKey(MediaSourceKind.JELLYFIN_LIBRARY, jellyfin_server, item_id),
            SourceRef(MediaSourceKind.JELLYFIN_LIBRARY, library) if library else None,
            jellyfin_item=entry,
        )
    except (MalformedMediaKey, InvalidPayload):
        return None


def _library(library_id: str | None) -> str | None:
    try:
        return canonical_guid(library_id) if library_id else None
    except InvalidPayload:
        return None


def _resolver(db: Session):
    """Resolve by path mapping; raises `UnresolvablePath`, which the evidence
    index reads as a file it cannot place, since a raw spelling could name
    another file here."""

    def resolve(path: str) -> str:
        return resolve_local_path(db, path).local_path

    return resolve


def decide_scans(
    db: Session,
    account: Account,
    media: Sequence[ScanMedia],
    now: datetime,
    *,
    cache: EvidenceCache = evidence_cache,
    evidence: ScanEvidence | None = None,
) -> list[MediaDecision]:
    """Decide every scan's media for one account, reading the linked server at most once."""

    if account.role is Role.OWNER:
        return [MediaAllowed(MediaBasis.OWNER)] * len(media)
    server = account.provider.server_id if account.provider is not None else UNKNOWN_SERVER
    targets = [scan_target(item, server) for item in media]
    unique = list({target.key: target for target in targets if target is not None}.values())
    decisions: dict[MediaKey, MediaDecision] = {}
    if unique:
        if evidence is not None:
            decisions = decide_media(
                load_subject(db, account),
                unique,
                user=evidence.value.user,
                catalog=evidence.value.catalog,
                libraries=evidence.value.libraries,
                items=evidence.items,
                now=now,
            )
        else:
            with cache.evidence(db, account, _resolver(db), now) as value:
                decisions = decide_media(
                    load_subject(db, account),
                    unique,
                    user=value.user,
                    catalog=value.catalog,
                    libraries=value.libraries,
                    items=value.items,
                    now=now,
                )
    unresolved = MediaDenied(MediaDenial.UNRESOLVED)
    return [decisions[target.key] if target is not None else unresolved for target in targets]
