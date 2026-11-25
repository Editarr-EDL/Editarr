"""Per-account community master links and their verified snapshots (local API §3, §4, §10)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityMasterFilterRow,
    CommunityMasterLinkRow,
    CommunityMasterSnapshotRow,
)
from scanner.modeling.community_master_diff import MasterFilter
from scanner.modeling.community_master_state import untouched_state

LIVE_STATES = ("active", "merged", "unavailable")


class CommunityMasterError(Exception):
    """A refused master-set change; `code` is the local API error code."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class LinkVersion:
    """The community version a link follows, as the match and snapshot describe it."""

    community_version_id: str
    version_status: str
    label: str | None = None
    alias_id: str | None = None
    community_work_id: str | None = None
    tier: str | None = None
    match_percent: int | None = None


@dataclass(frozen=True)
class VerifiedSnapshot:
    """A downloaded snapshot whose signature and receipt the caller already verified."""

    revision: int
    snapshot_digest: str
    snapshot_bytes: bytes
    signature: Mapping
    service_key_id: str
    receipt: Mapping
    receipt_id: str
    filters: tuple[MasterFilter, ...]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def compact(document) -> str:
    return json.dumps(document, separators=(",", ":"), sort_keys=True)


def get_link(db: Session, owner_id: int, link_id: str) -> CommunityMasterLinkRow | None:
    """The account's link, or None when it is unknown or belongs to another account."""
    link = db.get(CommunityMasterLinkRow, link_id)
    return link if link is not None and link.owner_id == owner_id else None


def require_link(db: Session, owner_id: int, link_id: str) -> CommunityMasterLinkRow:
    """Like `get_link`, but raise `not_found` so another account's link is never revealed."""
    link = get_link(db, owner_id, link_id)
    if link is None:
        raise CommunityMasterError("not_found")
    return link


def list_links(
    db: Session,
    owner_id: int,
    media_version_ids: Iterable[str] | None = None,
    *,
    include_replaced: bool = False,
) -> list[CommunityMasterLinkRow]:
    """The account's links, optionally only for some local versions, oldest first."""
    query = select(CommunityMasterLinkRow).where(CommunityMasterLinkRow.owner_id == owner_id)
    if media_version_ids is not None:
        query = query.where(CommunityMasterLinkRow.media_version_id.in_(list(media_version_ids)))
    if not include_replaced:
        query = query.where(CommunityMasterLinkRow.link_state != "replaced")
    return list(
        db.scalars(query.order_by(CommunityMasterLinkRow.created_at, CommunityMasterLinkRow.id))
    )


def live_link(
    db: Session, owner_id: int, media_version_id: str, service_url: str
) -> CommunityMasterLinkRow | None:
    """The account's one unreplaced link for a local version and service."""
    return db.scalar(
        select(CommunityMasterLinkRow).where(
            CommunityMasterLinkRow.owner_id == owner_id,
            CommunityMasterLinkRow.media_version_id == media_version_id,
            CommunityMasterLinkRow.service_url == service_url,
            CommunityMasterLinkRow.link_state != "replaced",
        )
    )


def store_snapshot(
    db: Session, link: CommunityMasterLinkRow, snapshot: VerifiedSnapshot, now: datetime
) -> CommunityMasterSnapshotRow:
    """Keep the exact verified bytes once per revision; a different copy is `snapshot_invalid`."""
    existing = db.scalar(
        select(CommunityMasterSnapshotRow).where(
            CommunityMasterSnapshotRow.link_id == link.id,
            CommunityMasterSnapshotRow.revision == snapshot.revision,
        )
    )
    if existing is not None:
        if (existing.snapshot_digest, existing.snapshot_bytes) != (
            snapshot.snapshot_digest,
            snapshot.snapshot_bytes,
        ):
            raise CommunityMasterError("snapshot_invalid")
        return existing
    row = CommunityMasterSnapshotRow(
        id=str(uuid.uuid4()),
        link_id=link.id,
        revision=snapshot.revision,
        snapshot_digest=snapshot.snapshot_digest,
        snapshot_bytes=snapshot.snapshot_bytes,
        signature_json=compact(snapshot.signature),
        service_key_id=snapshot.service_key_id,
        receipt_json=compact(snapshot.receipt),
        receipt_id=snapshot.receipt_id,
        downloaded_at=now,
    )
    db.add(row)
    db.flush()
    return row


def latest_snapshot(db: Session, link: CommunityMasterLinkRow) -> CommunityMasterSnapshotRow:
    """The stored snapshot of the link's latest revision, whose receipt votes use."""
    return db.scalar(
        select(CommunityMasterSnapshotRow).where(
            CommunityMasterSnapshotRow.link_id == link.id,
            CommunityMasterSnapshotRow.revision == link.latest_revision,
        )
    )


def prune_snapshots(db: Session, link: CommunityMasterLinkRow) -> None:
    """Keep only the latest revision and the baseline revisions rows still use."""
    in_use = set(
        db.scalars(
            select(CommunityMasterFilterRow.baseline_revision)
            .where(CommunityMasterFilterRow.link_id == link.id)
            .distinct()
        )
    )
    in_use.add(link.latest_revision)
    db.execute(
        delete(CommunityMasterSnapshotRow).where(
            CommunityMasterSnapshotRow.link_id == link.id,
            CommunityMasterSnapshotRow.revision.not_in(in_use),
        )
    )


def add_filter_row(
    db: Session, link_id: str, item: MasterFilter, revision: int, now: datetime
) -> CommunityMasterFilterRow:
    """Create an untouched row whose baseline is the served master filter."""
    row = CommunityMasterFilterRow(
        link_id=link_id,
        filter_id=item.filter_id,
        category=item.category,
        baseline_json=compact(item.values.as_json()),
        baseline_revision=revision,
        info_json=compact(dict(item.info)),
        state=untouched_state(item.values),
        locally_verified=False,
        local_touched=False,
        present=True,
        updated_at=now,
    )
    db.add(row)
    return row


def apply_master(
    db: Session,
    *,
    owner_id: int,
    media_version_id: str,
    service_url: str,
    contributor_id: str | None,
    version: LinkVersion,
    snapshot: VerifiedSnapshot,
    alignment_required: bool,
    replace: bool = False,
    now: datetime | None = None,
) -> CommunityMasterLinkRow:
    """Link a local version to a downloaded master set and create its filter rows.

    Fuzzy links (`alignment_required`) keep every row inactive until alignment is
    approved. The same community version again only stores a newer revision for
    review. Another version needs `replace=True`; the old link is then `replaced`
    and keeps its rows as history, and reviews that included it include the new one.
    """
    from .community_master_filters import refresh_local_verification
    from .community_master_updates import store_latest_snapshot

    now = now or utcnow()
    current = live_link(db, owner_id, media_version_id, service_url)
    if current is not None and current.community_version_id == version.community_version_id:
        store_latest_snapshot(db, owner_id, current.id, snapshot, now=now)
        return current
    if current is not None:
        if not replace:
            raise CommunityMasterError("replace_required")
        current.link_state = "replaced"
        current.updated_at = now
        db.flush()
    link = CommunityMasterLinkRow(
        id=str(uuid.uuid4()),
        owner_id=owner_id,
        media_version_id=media_version_id,
        service_url=service_url,
        contributor_id=contributor_id,
        community_version_id=version.community_version_id,
        community_work_id=version.community_work_id,
        label=version.label,
        alias_id=version.alias_id,
        version_status=version.version_status,
        tier=version.tier,
        match_percent=version.match_percent,
        applied_revision=snapshot.revision,
        latest_revision=snapshot.revision,
        alignment_state="required" if alignment_required else "not_required",
        link_state="active",
        created_at=now,
        updated_at=now,
    )
    db.add(link)
    db.flush()
    store_snapshot(db, link, snapshot, now)
    for item in snapshot.filters:
        add_filter_row(db, link.id, item, snapshot.revision, now)
    db.flush()
    refresh_local_verification(db, link)
    if current is not None:
        from .review_community_sources import move_link_sources

        move_link_sources(db, current.id, link.id)
    return link


def approve_alignment(
    db: Session,
    owner_id: int,
    link_id: str,
    alignment: Mapping,
    for_revision: int,
    now: datetime | None = None,
) -> CommunityMasterLinkRow:
    """Store an approved transform for `for_revision`; it activates a first import's rows
    and enables Accept for that revision's update rows."""
    link = require_link(db, owner_id, link_id)
    if for_revision != link.latest_revision:
        raise CommunityMasterError("update_changed")
    link.alignment_json = compact(dict(alignment))
    link.alignment_state = "approved"
    link.alignment_revision = for_revision
    link.updated_at = now or utcnow()
    db.flush()
    return link


def alignment_of(link: CommunityMasterLinkRow) -> dict | None:
    return json.loads(link.alignment_json) if link.alignment_json else None


def is_fuzzy(link: CommunityMasterLinkRow) -> bool:
    """A link below tier 3 needs local alignment before master bounds apply."""
    return link.alignment_state != "not_required"


def mark_link_state(
    db: Session,
    owner_id: int,
    link_id: str,
    state: str,
    *,
    merged_into: str | None = None,
    now: datetime | None = None,
) -> CommunityMasterLinkRow:
    """Record that sync found the version `merged` (into `merged_into`) or `unavailable`."""
    if state not in LIVE_STATES:
        raise ValueError(f"Unsupported link state {state!r}")
    link = require_link(db, owner_id, link_id)
    link.link_state = state
    link.merged_into = merged_into if state == "merged" else None
    link.updated_at = now or utcnow()
    db.flush()
    return link
