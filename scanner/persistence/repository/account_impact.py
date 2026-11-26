"""What deleting one account does to each of its resources, for preview and execution.

`build_impact` reads the target's state under the writer lock. Preview and
execution call it the same way, so an unchanged state reproduces the consent the
actor confirmed. The impact is the full internal inventory; `visible` records
which reviews and scans the actor may see, and only those are ever named to them.
Retention and transfer need no per-resource authority. Permanent deletion
classifies every review and scan with the actor's captured media evidence and
reuses MU-36's `DeletionPlan` for published files.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from scanner.db import (
    ApiKeyRow,
    AuthSessionRow,
    CommunityIdentityRow,
    CommunityOperationRow,
    MediaItemExportRow,
    MediaItemExportScanRow,
    SavedReviewRow,
    ScanRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.formats.export_layout import ExportId
from scanner.persistence.rows.db_account_links import OPEN_STATES, AccountLinkTransitionRow
from scanner.persistence.rows.db_policies import ExportScope

from .account_community_masters import MasterImpact, master_impact
from .export_identity_moves import PlannedMove
from .review_disposition import DeletionPlan
from .scan_jobs import ScanJobFacts

WORKER_HELD = frozenset({"acquiring_model", "scanning", "orphaned", "cancelling"})
TERMINAL = frozenset({"complete", "failed", "cancelled"})
PENDING_COMMUNITY = ("pending", "submitted")


class Disposition(StrEnum):
    RETAIN = "retain"
    TRANSFER = "transfer"
    DELETE = "delete"


class Blocker(StrEnum):
    """Why a disposition cannot run now. Each carries only a count, never hidden details.

    A transfer is also blocked by each MU-35 move conflict code (for example
    `export_filename_collision`) with the number of files it affects.
    """

    LINK_IN_PROGRESS = "link_in_progress"
    JOBS_RUNNING = "account_jobs_running"
    REVIEWS_OUTSIDE_ACCESS = "reviews_outside_access"
    SCANS_OUTSIDE_ACCESS = "scans_outside_access"
    TRANSFER_MEDIA_INACCESSIBLE = "transfer_media_inaccessible"
    TRANSFER_UNSETTLED = "account_transfer_unsettled"
    """Moves of an earlier transfer that recovery could not yet undo."""


class AccountDispositionConflict(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass(frozen=True)
class ScanImpact:
    scan_id: str
    status: str
    required: bool
    """Another account's review, a publication outside the account's reviews, or pending
    community work references it, so permanent deletion keeps it."""


@dataclass(frozen=True)
class Visibility:
    """The account's reviews, scans, and publications the actor may see; the rest are counts."""

    reviews: frozenset[str]
    scans: frozenset[str]
    exports: frozenset[int]


@dataclass(frozen=True)
class Transfer:
    destination_id: int
    export_id: ExportId
    """The destination's export ID, where the account's personal files move."""
    moves: tuple[PlannedMove, ...]
    """Personal publications targeting the account, moving to the destination's export ID."""
    policy_exports: tuple[int, ...]
    """Detached personal publications whose policy owner is the account; the destination
    becomes their intended policy owner."""
    media_denied: int
    """Reviews on media the destination may not access."""


@dataclass(frozen=True)
class AccountImpact:
    user_id: int
    mode: Disposition
    role: str
    reviews: tuple[str, ...]
    plans: tuple[DeletionPlan, ...]
    """Delete mode only: one MU-36 plan per review, in review ID order."""
    scans: tuple[ScanImpact, ...]
    """Delete mode only: every scan the account started, classified."""
    waiting_jobs: tuple[str, ...]
    running_jobs: tuple[str, ...]
    transfer: Transfer | None
    personal_files: int
    sessions: int
    api_key: bool
    linked: bool
    community_identity: bool
    community_operations: int
    community_pending: int
    blockers: dict[str, int]
    visible: Visibility
    community_masters: MasterImpact = MasterImpact()


# Returns the MU-36 plan of one review the caller may delete, or None when it may not.
PlanReview = Callable[[str], DeletionPlan | None]
# Returns whether the caller may delete one of the account's scans.
AllowScan = Callable[[ScanJobFacts | None], bool]
# Plans the transfer of the account's reviews and files under the writer lock.
PlanTransfer = Callable[[tuple[str, ...]], Transfer]


def account_reviews(db: Session, user_id: int) -> tuple[str, ...]:
    return tuple(
        db.scalars(
            select(SavedReviewRow.id)
            .where(SavedReviewRow.owner_id == user_id)
            .order_by(SavedReviewRow.id)
        )
    )


def account_scans(db: Session, user_id: int) -> tuple[str, ...]:
    return tuple(
        db.scalars(select(ScanRow.id).where(ScanRow.initiator_id == user_id).order_by(ScanRow.id))
    )


def personal_files(user_id: int):
    """Personal publications targeting the account or following its policy."""
    return (
        select(MediaItemExportRow.id)
        .outerjoin(SavedReviewRow, SavedReviewRow.id == MediaItemExportRow.review_id)
        .where(MediaItemExportRow.scope == ExportScope.PERSONAL)
        .where(
            or_(
                MediaItemExportRow.target_user_id == user_id,
                SavedReviewRow.owner_id == user_id,
                and_(
                    MediaItemExportRow.review_id.is_(None),
                    func.coalesce(
                        MediaItemExportRow.intended_policy_owner_id,
                        MediaItemExportRow.policy_owner_id,
                    )
                    == user_id,
                ),
            )
        )
    )


def count(db: Session, statement) -> int:
    return db.scalar(select(func.count()).select_from(statement.subquery()))


def _required(db: Session, user_id: int, scan_id: str) -> bool:
    from .repository_review_operations import _referencing_reviews

    other_review = db.scalar(
        select(SavedReviewRow.id)
        .where(_referencing_reviews(db, scan_id))
        .where(or_(SavedReviewRow.owner_id.is_(None), SavedReviewRow.owner_id != user_id))
        .limit(1)
    )
    outside_export = db.scalar(
        select(MediaItemExportRow.id)
        .join(MediaItemExportScanRow)
        .outerjoin(SavedReviewRow, SavedReviewRow.id == MediaItemExportRow.review_id)
        .where(MediaItemExportScanRow.scan_id == scan_id)
        .where(or_(SavedReviewRow.owner_id.is_(None), SavedReviewRow.owner_id != user_id))
        .limit(1)
    )
    community = db.scalar(
        select(CommunityOperationRow.operation_id)
        .where(CommunityOperationRow.scan_id == scan_id)
        .where(CommunityOperationRow.state.in_(PENDING_COMMUNITY))
        .limit(1)
    )
    return other_review is not None or outside_export is not None or community is not None


def _open_links(db: Session, user_ids) -> int:
    return count(
        db,
        select(AccountLinkTransitionRow.id)
        .where(AccountLinkTransitionRow.user_id.in_(user_ids))
        .where(AccountLinkTransitionRow.state.in_([state.value for state in OPEN_STATES])),
    )


def build_impact(
    db: Session,
    user_id: int,
    mode: Disposition,
    visible: Visibility,
    *,
    plan_review: PlanReview | None = None,
    scan_facts: dict[str, ScanJobFacts | None] | None = None,
    allow_scan: AllowScan | None = None,
    plan_transfer: PlanTransfer | None = None,
    unsettled: int = 0,
) -> AccountImpact:
    """Describe the disposition of `user_id`.

    Delete mode needs `plan_review`, `scan_facts`, and `allow_scan`; transfer mode
    needs `plan_transfer`. `unsettled` counts moves of an earlier transfer that
    recovery left open; they block every mode.
    """
    user = db.get(UserRow, user_id)
    reviews = account_reviews(db, user_id)
    scan_ids = account_scans(db, user_id)
    statuses = {
        row.id: row.status
        for row in db.execute(select(ScanRow.id, ScanRow.status).where(ScanRow.id.in_(scan_ids)))
    }
    blockers: dict[str, int] = {}

    def block(code: str, amount: int) -> None:
        if amount:
            blockers[code] = blockers.get(code, 0) + amount

    running = tuple(scan for scan in scan_ids if statuses[scan] in WORKER_HELD)
    waiting = tuple(scan for scan in scan_ids if statuses[scan] not in WORKER_HELD | TERMINAL)
    plans: tuple[DeletionPlan, ...] = ()
    scans: tuple[ScanImpact, ...] = ()
    transfer = None
    linked_accounts = [user_id]
    if mode is Disposition.DELETE:
        planned = [plan_review(review) for review in reviews]
        plans = tuple(plan for plan in planned if plan is not None)
        block(Blocker.REVIEWS_OUTSIDE_ACCESS, len(planned) - len(plans))
        block(
            Blocker.SCANS_OUTSIDE_ACCESS,
            sum(1 for scan in scan_ids if not allow_scan(scan_facts[scan])),
        )
        block(Blocker.JOBS_RUNNING, len(running))
        scans = tuple(
            ScanImpact(scan, statuses[scan], _required(db, user_id, scan)) for scan in scan_ids
        )
    elif mode is Disposition.TRANSFER:
        transfer = plan_transfer(reviews)
        linked_accounts.append(transfer.destination_id)
        block(Blocker.REVIEWS_OUTSIDE_ACCESS, len(set(reviews) - visible.reviews))
        block(Blocker.TRANSFER_MEDIA_INACCESSIBLE, transfer.media_denied)
        for move in transfer.moves:
            if move.conflict is not None:
                block(move.conflict, 1)
    block(Blocker.LINK_IN_PROGRESS, _open_links(db, linked_accounts))
    block(Blocker.TRANSFER_UNSETTLED, unsettled)
    operations = select(CommunityOperationRow.operation_id).where(
        CommunityOperationRow.owner_id == user_id
    )
    return AccountImpact(
        user_id=user_id,
        mode=mode,
        role=user.role,
        reviews=reviews,
        plans=plans,
        scans=scans,
        waiting_jobs=waiting,
        running_jobs=running,
        transfer=transfer,
        personal_files=count(db, personal_files(user_id)),
        sessions=count(
            db,
            select(AuthSessionRow.id)
            .where(AuthSessionRow.user_id == user_id)
            .where(AuthSessionRow.revoked_at.is_(None)),
        ),
        api_key=db.scalar(select(ApiKeyRow.id).where(ApiKeyRow.user_id == user_id)) is not None,
        linked=db.get(UserProviderIdentityRow, user_id) is not None,
        community_identity=db.scalar(
            select(CommunityIdentityRow.credential_ref).where(
                CommunityIdentityRow.user_id == user_id
            )
        )
        is not None,
        community_operations=count(db, operations),
        community_pending=count(
            db, operations.where(CommunityOperationRow.state.in_(PENDING_COMMUNITY))
        ),
        blockers=blockers,
        visible=visible,
        community_masters=master_impact(db, user_id),
    )
