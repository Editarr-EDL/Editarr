"""An account's community master-set state under deletion and transfer (MU-36 style).

Master links and their notifications, display preferences, and daily sync state
belong to one account. Deletion removes them with the account's other personal
state; transfer gives them to the destination without overwriting its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityMasterDismissalRow,
    CommunityMasterFilterRow,
    CommunityMasterLinkRow,
    CommunityMasterPreferenceRow,
    CommunityMasterSnapshotRow,
    CommunityMasterUpdateRow,
    CommunitySyncNotificationRow,
    CommunitySyncStateRow,
)

from .community_master_links import live_link
from .review_community_sources import detach_link_sources, move_link_sources

_LINK_CHILDREN = (
    CommunitySyncNotificationRow,
    CommunityMasterSnapshotRow,
    CommunityMasterFilterRow,
    CommunityMasterUpdateRow,
    CommunityMasterDismissalRow,
)


@dataclass(frozen=True)
class MasterImpact:
    """What an account holds: links (including replaced history), notifications,
    whether it has preferences, and its per-service sync states."""

    links: int = 0
    notifications: int = 0
    preferences: bool = False
    sync_states: int = 0


def _count(db: Session, row, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(row).where(row.owner_id == user_id))


def master_impact(db: Session, user_id: int) -> MasterImpact:
    return MasterImpact(
        links=_count(db, CommunityMasterLinkRow, user_id),
        notifications=_count(db, CommunitySyncNotificationRow, user_id),
        preferences=db.get(CommunityMasterPreferenceRow, user_id) is not None,
        sync_states=_count(db, CommunitySyncStateRow, user_id),
    )


def delete_master_state(db: Session, user_id: int) -> int:
    """Delete the account's master-set state; return how many links it removed.

    Each link's snapshots, filter rows, updates, dismissals and notifications go first,
    so the result does not depend on foreign-key enforcement.
    """
    links = select(CommunityMasterLinkRow.id).where(CommunityMasterLinkRow.owner_id == user_id)
    detach_link_sources(db, db.scalars(links).all())
    db.execute(
        delete(CommunitySyncNotificationRow).where(CommunitySyncNotificationRow.owner_id == user_id)
    )
    for row in _LINK_CHILDREN:
        db.execute(delete(row).where(row.link_id.in_(links.scalar_subquery())))
    removed = db.execute(
        delete(CommunityMasterLinkRow).where(CommunityMasterLinkRow.owner_id == user_id)
    ).rowcount
    db.execute(
        delete(CommunityMasterPreferenceRow).where(CommunityMasterPreferenceRow.owner_id == user_id)
    )
    db.execute(delete(CommunitySyncStateRow).where(CommunitySyncStateRow.owner_id == user_id))
    db.flush()
    return removed


def transfer_master_state(db: Session, user_id: int, destination_id: int) -> int:
    """Give the account's master-set state to `destination_id`; return the links moved.

    A link for a local version and service the destination already follows becomes
    `replaced` history, so the destination keeps its own. Preferences and sync
    state move only where the destination has none; otherwise they are dropped.
    """
    links = list(
        db.scalars(select(CommunityMasterLinkRow).where(CommunityMasterLinkRow.owner_id == user_id))
    )
    for link in links:
        if link.link_state != "replaced" and (
            kept := live_link(db, destination_id, link.media_version_id, link.service_url)
        ):
            link.link_state = "replaced"
            move_link_sources(db, link.id, kept.id)
        link.owner_id = destination_id
        db.flush()
    db.execute(
        update(CommunitySyncNotificationRow)
        .where(CommunitySyncNotificationRow.owner_id == user_id)
        .values(owner_id=destination_id)
    )
    own = db.get(CommunityMasterPreferenceRow, user_id)
    if own is not None:
        if db.get(CommunityMasterPreferenceRow, destination_id) is None:
            db.execute(
                update(CommunityMasterPreferenceRow)
                .where(CommunityMasterPreferenceRow.owner_id == user_id)
                .values(owner_id=destination_id)
            )
        else:
            db.delete(own)
    for state in db.scalars(
        select(CommunitySyncStateRow).where(CommunitySyncStateRow.owner_id == user_id)
    ).all():
        if db.get(CommunitySyncStateRow, (destination_id, state.service_url)) is None:
            db.execute(
                update(CommunitySyncStateRow)
                .where(
                    CommunitySyncStateRow.owner_id == user_id,
                    CommunitySyncStateRow.service_url == state.service_url,
                )
                .values(owner_id=destination_id)
            )
        else:
            db.delete(state)
    db.flush()
    db.expire_all()
    return len(links)
