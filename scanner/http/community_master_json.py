"""camelCase JSON bodies of the master-set routes (local API §3–§9)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from scanner.db import CommunityMasterLinkRow
from scanner.modeling.community_master_state import Preferences
from scanner.persistence.repository.community_master_filters import MasterFilterView
from scanner.persistence.repository.community_master_links import alignment_of
from scanner.persistence.repository.community_master_sync import SyncStatus
from scanner.persistence.repository.community_master_updates import (
    UpdateRowView,
    UpdateView,
    pending_counts,
)

UNLABELED = "Other"


def epoch(value: datetime | None) -> int | None:
    """Unix seconds of a stored naive-UTC time."""
    if value is None:
        return None
    return int(value.replace(tzinfo=timezone.utc).timestamp())


def link_json(db: Session, link: CommunityMasterLinkRow) -> dict:
    """`MasterLink` and `MasterLinkSummary`; `linkState` and `mergedInto` report a link
    whose version was merged, became unavailable or was replaced."""
    return {
        "linkId": link.id,
        "localVersionId": link.media_version_id,
        "communityVersionId": link.community_version_id,
        "label": link.label or UNLABELED,
        "aliasId": link.alias_id,
        "status": link.version_status,
        "unconfirmed": link.version_status == "candidate",
        "tier": link.tier,
        "matchPercent": link.match_percent,
        "appliedRevision": link.applied_revision,
        "latestRevision": link.latest_revision,
        "alignmentState": link.alignment_state,
        "alignment": alignment_of(link),
        "alignmentRevision": link.alignment_revision,
        "pendingUpdate": pending_counts(db, link.id),
        "linkState": link.link_state,
        "mergedInto": link.merged_into,
    }


def preferences_json(preferences: Preferences) -> dict:
    return {
        "confidenceThreshold": preferences.confidence_threshold,
        "showLowConfidence": preferences.show_low_confidence,
    }


def filter_json(view: MasterFilterView) -> dict:
    return {
        "filterId": view.filter_id,
        "category": view.category,
        "master": view.master,
        "local": view.local,
        "effective": view.effective,
        "state": view.state,
        "touched": view.touched,
        "locallyVerified": view.locally_verified,
        "myVote": view.my_vote,
        "active": view.active,
    }


def _row_json(row: UpdateRowView) -> dict:
    return {
        "rowId": row.row_id,
        "filterId": row.filter_id,
        "category": row.category,
        "touched": row.touched,
        "changedFields": list(row.changed_fields),
        "mine": row.mine,
        "oldMaster": row.old_master,
        "newMaster": row.new_master,
        "mergedInto": row.merged_into,
        "allowed": list(row.allowed),
        "canAccept": row.can_accept,
    }


def update_json(view: UpdateView) -> dict:
    """`GET …/update`; every bound is on the local timeline through the stored alignment."""
    return {
        "version": 1,
        "fromRevision": view.from_revision,
        "toRevision": view.to_revision,
        "alignmentRequired": view.alignment_required,
        "alignmentApproved": view.alignment_approved,
        "groups": {group: [_row_json(row) for row in rows] for group, rows in view.groups.items()},
    }


def _notification_json(row, place) -> dict:
    locator, item = place
    return {
        "notificationId": row.id,
        "linkId": row.link_id,
        "libraryId": item.library_id,
        "itemId": item.jellyfin_id,
        "mediaSourceId": locator.media_source_id or None,
        "title": item.name,
        "kind": row.kind,
        "fromRevision": row.from_revision,
        "toRevision": row.to_revision,
        "createdAt": epoch(row.created_at),
        "read": row.read_at is not None,
    }


def sync_json(status: SyncStatus, *, ran: bool, locate) -> dict:
    """The sync status (§8).

    `locate(link_id)` returns the `(locator, media item)` the caller may open for a
    notification's local version, or None to leave the notification out; hidden
    unread notifications are not counted either.
    """
    places: dict = {}
    shown, hidden_unread = [], 0
    for row in status.notifications:
        if row.link_id not in places:
            places[row.link_id] = locate(row.link_id)
        if places[row.link_id] is None:
            hidden_unread += row.read_at is None
        else:
            shown.append(_notification_json(row, places[row.link_id]))
    return {
        "version": 1,
        "ran": ran,
        "state": status.state,
        "lastRunAt": epoch(status.last_run_at),
        "nextDueAt": epoch(status.next_due_at),
        "lastError": status.last_error,
        "unreadCount": status.unread_count - hidden_unread,
        "notifications": shown,
    }
