"""Attribute new local activity and assign activity awaiting an owner at setup or restore."""

from dataclasses import dataclass

from sqlalchemy import Connection, case, exists, select, update
from sqlalchemy.orm import Session, aliased

from scanner.db import (
    CommunityIdentityRow,
    CommunityOperationRow,
    MediaItemExportRow,
    SavedReviewRow,
    ScanRow,
    UserRow,
)
from scanner.persistence.archive import live_only


@dataclass(frozen=True)
class ActivityOwner:
    user_id: int
    permission_revision: int


def activity_owner(db: Session | Connection) -> ActivityOwner | None:
    """Return the owner, credited with activity that no request principal started.

    Request handlers name their principal (`review_actor`, a scan initiator, or a
    publication's access). Upgrade, restore, and owner-run commands fall back to
    the owner. Before owner setup it is None and new rows await an owner.
    """
    row = db.execute(
        select(UserRow.id, UserRow.permission_revision).where(UserRow.role == "owner")
    ).one_or_none()
    return ActivityOwner(row.id, row.permission_revision) if row is not None else None


def new_review_owner_columns(db: Session) -> dict[str, int | None]:
    """Columns for a review the acting account creates, and so also authors."""
    owner = db.info.get("review_actor") or activity_owner(db)
    user_id = owner.user_id if owner is not None else None
    return {"owner_id": user_id, "original_author_id": user_id}


def new_scan_initiator_columns(db: Session) -> dict[str, int | None]:
    """Columns recording who started a scan job or created an imported or manual scan."""
    owner = db.info.get("review_actor") or activity_owner(db)
    return {
        "initiator_id": owner.user_id if owner is not None else None,
        "initiator_permission_revision": owner.permission_revision if owner is not None else None,
    }


def assign_awaiting_activity(db: Session | Connection, owner: ActivityOwner) -> None:
    """Assign every review, scan, export record, and community identity and history that
    awaits an owner, without committing.

    Owner setup and restore composition call this; revisions 0011 and 0013 apply the
    same assignment when an owner already exists. An export without a source review
    keeps no policy owner or author. A community operation's actor stays unrecorded.
    Archived activity stays as it was archived.
    """
    db.execute(
        update(SavedReviewRow)
        .where(SavedReviewRow.owner_id.is_(None), live_only(SavedReviewRow))
        .values(owner_id=owner.user_id, original_author_id=owner.user_id)
    )
    db.execute(
        update(ScanRow)
        .where(ScanRow.initiator_id.is_(None), live_only(ScanRow))
        .values(
            initiator_id=owner.user_id,
            initiator_permission_revision=owner.permission_revision,
        )
    )
    reviewed = case((MediaItemExportRow.review_id.is_(None), None), else_=owner.user_id)
    db.execute(
        update(MediaItemExportRow)
        .where(MediaItemExportRow.actor_id.is_(None), live_only(MediaItemExportRow))
        .values(actor_id=owner.user_id, policy_owner_id=reviewed, original_author_id=reviewed)
    )
    owned = exists().where(aliased(CommunityIdentityRow).user_id == owner.user_id)
    db.execute(
        update(CommunityIdentityRow)
        .where(CommunityIdentityRow.user_id.is_(None), ~owned)
        .values(user_id=owner.user_id)
    )
    db.execute(
        update(CommunityOperationRow)
        .where(CommunityOperationRow.owner_id.is_(None))
        .values(owner_id=owner.user_id)
    )
