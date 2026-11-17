"""Media read authorization for catalog, artwork, candidate, and track routes.

A handler depends on `catalog_viewer(capability)`, which loads the live
principal and refuses a caller without the capability before anything is
read. The `CatalogViewer` decides source-server records through MU-11's
`decide_media` with MU-16's evidence, opened once per request and closed with
it. Handlers decide before they count, facet, decorate, page, or read a cache
entry, so shared caches hold only unfiltered server reads and no filtered
result is cached. Records leave without server paths unless the caller holds
`media.paths.view`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TypeVar

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.integrations.jellyfin_user_snapshot import (
    ItemAccessSnapshot,
    JellyfinUserSnapshot,
    Unverified,
    UnverifiedReason,
)
from scanner.models import JellyfinLibrary, JellyfinMediaItem
from scanner.persistence.repository.repository_paths import resolve_local_path
from scanner.persistence.rows.db_policies import MediaSourceKind
from scanner.security.access import link_state
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import ProviderIdentity
from scanner.security.access.records import (
    MediaAllowed,
    MediaBasis,
    MediaDecision,
    MediaDenial,
    MediaDenied,
)
from scanner.security.access.request_access import request_access
from scanner.security.access.targets import MediaRef
from scanner.security.jellyfin_accounts import store
from scanner.security.jellyfin_accounts.evidence import CachedItems, MediaEvidence, evidence_cache
from scanner.security.jellyfin_accounts.sync import SYNC_INTERVAL
from scanner.security.media_access.keys import SourceRef
from scanner.security.media_access.matching import MediaTarget
from scanner.security.media_access.resolver import LinkedSubject, LocalSubject, OwnerSubject
from scanner.security.media_access.service import decide_media, load_subject
from scanner.server_paths import parse_server_path

from .catalog_artwork import artwork_url

R = TypeVar("R")


_REACHABLE = MediaRef(MediaAllowed(MediaBasis.LOCAL_DEFAULT))
"""A media target the caller may reach, for deciding the capability before any read."""

_UNNAMED = MediaDenied(MediaDenial.UNRESOLVED)
"""The decision for a record that names no single item."""


@dataclass
class RecentItems:
    """Jellyfin item decisions reused for `window`, never from before the user's last sync.

    Missing or older decisions are read through MU-16's `CachedItems`. Its
    outage fallback must satisfy the same sync lower bound as fast reuse.
    """

    items: CachedItems
    since: datetime | None
    window: timedelta = SYNC_INTERVAL

    def item_access(
        self, identity: ProviderIdentity, item_ids: Sequence[str]
    ) -> ItemAccessSnapshot | Unverified:
        now = self.items.now
        recent = {}
        for item_id in item_ids:
            known = self.items.decisions.get((identity, item_id))
            if (
                known is not None
                and self.since is not None
                and known.verified_at >= self.since
                and link_state.elapsed_within(known.verified_at, now, self.window)
            ):
                recent[item_id] = known
        missing = [item_id for item_id in item_ids if item_id not in recent]
        answer = self.items.item_access(identity, missing) if missing else None
        if isinstance(answer, ItemAccessSnapshot) and (
            answer.identity != identity or self.since is None or answer.verified_at < self.since
        ):
            answer = Unverified(UnverifiedReason.UNREACHABLE)
        if isinstance(answer, Unverified) and (
            answer.reason is UnverifiedReason.SERVER_MISMATCH or not recent
        ):
            return answer
        decisions = {item_id: known.access for item_id, known in recent.items()}
        times = [known.verified_at for known in recent.values()]
        if isinstance(answer, ItemAccessSnapshot):
            decisions |= answer.decisions
            times.append(answer.verified_at)
        return ItemAccessSnapshot(identity, decisions, min(times))


@dataclass
class CatalogViewer:
    """One request's live principal deciding the media it may reach under `capability`."""

    access: PrincipalAccess
    db: Session
    capability: Capability
    now: datetime
    _evidence: MediaEvidence | None = None
    _subject: object = None
    _resolved: dict[str, str] = field(default_factory=dict)
    session_id: int | None = None

    @property
    def export_caller(self):
        from scanner.persistence.repository.review_access import ReviewAccess

        return ReviewAccess(self.db, self.access, self.session_id)

    @property
    def is_owner(self) -> bool:
        return isinstance(self.subject, OwnerSubject)

    @property
    def artwork_scope(self) -> str:
        principal = self.access.principal
        key = f"catalog-artwork-2:{principal.user_id}:{principal.permission_revision}"
        return hashlib.sha256(key.encode()).hexdigest()

    @property
    def subject(self):
        if self._subject is None:
            self._subject = load_subject(self.db, self.access.principal.account)
        return self._subject

    def sees_paths(self) -> bool:
        """Whether server paths may appear in this caller's records and errors."""

        return Capability.MEDIA_PATHS_VIEW in self.access.capabilities

    def require_capability(self, capability: Capability) -> None:
        """Check a capability before resolving or inspecting a supplied media path."""
        found = decide(self.access, capability, _REACHABLE)
        if not isinstance(found, Allowed):
            raise AccessDenied(found.code)

    def resolve(self, path: str) -> str:
        """The local path the path mappings give a server path, read once per request."""

        if path not in self._resolved:
            self._resolved[path] = resolve_local_path(self.db, path).local_path
        return self._resolved[path]

    def jellyfin_server(self, fallback: str) -> str:
        """The server key for Jellyfin targets: the linked server, else `fallback`."""

        account = self.access.principal.account
        return account.provider.server_id if account.provider is not None else fallback

    def library_visible(self, library_id: str) -> bool:
        """Whether the caller may open a Jellyfin library's listing at all.

        Local accounts need the library in their scope; linked accounts need
        the last verified Jellyfin policy to open it. Items inside are still
        decided one by one.
        """

        subject = self.subject
        if isinstance(subject, OwnerSubject):
            return True
        if isinstance(subject, LocalSubject):
            try:
                source = SourceRef(MediaSourceKind.JELLYFIN_LIBRARY, library_id)
            except ValueError:
                return False
            return subject.scope.all_sources or source in subject.scope.sources
        user = store.stored_snapshot(self.db, self.access.principal.user_id)
        return (
            isinstance(subject, LinkedSubject)
            and isinstance(user, JellyfinUserSnapshot)
            and user.identity == subject.identity
            and link_state.elapsed_within(user.verified_at, self.now)
            and user.media.allows_library(library_id)
        )

    def decide(self, targets: Sequence[MediaTarget]) -> dict:
        """One `decide_media` call for every target, reading Jellyfin at most once."""

        if not targets:
            return {}
        evidence = self._open()
        since = (
            evidence.user.verified_at if isinstance(evidence.user, JellyfinUserSnapshot) else None
        )
        items = RecentItems(evidence.items, since) if evidence.items is not None else None
        return decide_media(
            self.subject,
            targets,
            user=evidence.user,
            catalog=evidence.catalog,
            libraries=evidence.libraries,
            items=items,
            now=self.now,
        )

    def permits(self, decision: MediaDecision) -> bool:
        return isinstance(decide(self.access, self.capability, MediaRef(decision)), Allowed)

    def visible(
        self, records: Sequence[R], target_of: Callable[[R], MediaTarget | None]
    ) -> list[R]:
        """The records the caller may reach, in order. The owner's pass without a decision."""

        if self.is_owner:
            return list(records)
        targets = [target_of(record) for record in records]
        decisions = self.decide([target for target in targets if target is not None])
        return [
            record
            for record, target in zip(records, targets, strict=True)
            if self.permits(decisions[target.key] if target is not None else _UNNAMED)
        ]

    def require(self, target: MediaTarget | None, capability: Capability | None = None) -> None:
        """Refuse a direct request for media the caller may not reach, as `not_found`."""

        if self.is_owner:
            return
        decision = self.decide([target])[target.key] if target is not None else _UNNAMED
        found = decide(self.access, capability or self.capability, MediaRef(decision))
        if not isinstance(found, Allowed):
            raise AccessDenied(found.code)

    def _open(self) -> MediaEvidence:
        if self._evidence is None:
            account = self.access.principal.account
            self._evidence = evidence_cache.evidence(self.db, account, self.resolve, self.now)
        return self._evidence

    def close(self) -> None:
        if self._evidence is not None:
            self._evidence.close()


def catalog_viewer(capability: Capability) -> Callable[..., Iterator[CatalogViewer]]:
    """FastAPI dependency factory: refuse a caller without `capability` before any read."""

    def dependency(
        request: Request,
        access: PrincipalAccess = Depends(request_access),
        db: Session = Depends(get_db),
    ) -> Iterator[CatalogViewer]:
        found = decide(access, capability, _REACHABLE)
        if not isinstance(found, Allowed):
            raise AccessDenied(found.code)
        viewer = CatalogViewer(
            access,
            db,
            capability,
            link_state.link_clock(),
            session_id=request.state.access_credential.session_id,
        )
        try:
            yield viewer
        finally:
            viewer.close()

    return dependency


def not_found() -> AccessDenied:
    return AccessDenied(AccessErrorCode.NOT_FOUND)


def _file_name(path: str | None) -> str | None:
    parsed = parse_server_path(path) if path else None
    return (parsed.names[-1] or None) if parsed and parsed.names else None


def jellyfin_record(item: JellyfinMediaItem, viewer: CatalogViewer) -> JellyfinMediaItem:
    """Expose filenames for reachable files and redact full paths when required."""
    sees_paths = viewer.sees_paths()
    updates: dict[str, object] = {
        "versions": [
            version.model_copy(
                update={
                    "file_name": _file_name(version.path),
                    "path": version.path if sees_paths else None,
                }
            )
            for version in item.versions
        ],
    }
    if item.image_url:
        updates["image_url"] = artwork_url(item.image_url, viewer)
    if not sees_paths:
        updates["path"] = None
        updates["media_streams"] = [
            {key: value for key, value in stream.items() if key.casefold() != "path"}
            for stream in item.media_streams
        ]
    return item.model_copy(update=updates)


def library_record(library: JellyfinLibrary, viewer: CatalogViewer) -> JellyfinLibrary:
    return library if viewer.sees_paths() else library.model_copy(update={"locations": []})
