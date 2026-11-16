"""Transfer a deleted account's reviews, policy, and personal files to another account.

Rulings of 2026-10-28 (wave13-mu38-transfer): the destination is any active,
undeleted account other than the target that the actor can see. Reviews on media
the destination cannot access block the whole transfer with a count. Personal
files targeting the account move to the destination's export ID through MU-35's
move machinery, and the destination becomes the policy owner of everything that
followed the account's policy, so regeneration applies the destination's policy.
"""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scanner.db import MediaItemExportRow, SavedReviewRow
from scanner.persistence.rows.db_policies import ExportScope
from scanner.security.access.authorization import resolve_account
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import Account, AccountStatus, CredentialKind, UserId
from scanner.security.access.load import load_access
from scanner.security.access.records import MediaAllowed
from scanner.security.access.vocabulary import Role
from scanner.security.scan_jobs.media import ScanEvidence, decide_scans, gather_evidence

from . import account_transfer_files as transfer_files
from .account_impact import AccountDispositionConflict, Transfer
from .export_identity_moves import plan_moves, switch_rows
from .export_regeneration import queue_regenerations, save_snapshot
from .scan_jobs import ScanMedia


@dataclass(frozen=True)
class Receiver:
    """The requested destination and its media evidence, gathered before the writer lock."""

    user_id: int
    evidence: dict[ScanMedia, ScanEvidence]


def _account(db: Session, user_id: int) -> Account | None:
    access = load_access(db, UserId(user_id), CredentialKind.SESSION)
    return access.principal.account if access is not None else None


def prepare_receiver(db: Session, destination_id: int, media) -> Receiver:
    """Read the destination's provider evidence for `media` outside the writer lock."""
    account = _account(db, destination_id)
    evidence = {}
    if account is not None and account.role is not Role.OWNER:
        now = datetime.now(UTC)
        evidence = {item: gather_evidence(db, account, item, now) for item in media}
    db.rollback()
    return Receiver(destination_id, evidence)


def destination(
    db: Session, request: PrincipalAccess, target_id: int, receiver: Receiver
) -> Account:
    """The destination's current account, or one refusal for every unusable destination.

    A hidden, missing, deleted, disabled, or target destination answers alike, so
    the refusal reveals nothing the actor may not see.
    """
    unavailable = AccountDispositionConflict(
        409, "transfer_destination_unavailable", "Choose an active account you can see"
    )
    if receiver.user_id == target_id:
        raise unavailable
    try:
        resolve_account(db, request, receiver.user_id, C.ACCOUNTS_DELETE_OTHER)
    except AccessDenied:
        raise unavailable from None
    account = _account(db, receiver.user_id)
    if account is None or account.status is not AccountStatus.ACTIVE:
        raise unavailable
    return account


def _allowed(db: Session, account: Account, receiver: Receiver, item: ScanMedia) -> bool:
    if account.role is Role.OWNER:
        return True
    evidence = receiver.evidence.get(item)
    if evidence is None:
        return False
    decision = decide_scans(db, account, [item], datetime.now(UTC), evidence=evidence)[0]
    return isinstance(decision, MediaAllowed)


def _with_own_placements(db: Session, user_id: int, moves):
    """A new path holding this transfer's own placed file is not a collision."""
    return tuple(
        replace(move, conflict=None)
        if move.conflict == "export_file_collision"
        and transfer_files.placed(db, user_id, move.new_path)
        else move
        for move in moves
    )


def plan_transfer(
    db: Session,
    target_id: int,
    account: Account,
    receiver: Receiver,
    reviews: tuple[str, ...],
    review_media: dict[str, list[ScanMedia]],
) -> Transfer:
    """Decide the transfer to `account` under the writer lock; reads only."""
    denied = sum(
        1
        for review in reviews
        if review not in review_media
        or not all(_allowed(db, account, receiver, item) for item in review_media[review])
    )
    moves = tuple(
        move
        for move in plan_moves(db, target_id, account.export_id)
        if move.old_path != move.new_path
    )
    policy = tuple(
        db.scalars(
            select(MediaItemExportRow.id)
            .where(MediaItemExportRow.scope == ExportScope.PERSONAL)
            .where(MediaItemExportRow.review_id.is_(None))
            .where(
                func.coalesce(
                    MediaItemExportRow.intended_policy_owner_id, MediaItemExportRow.policy_owner_id
                )
                == target_id
            )
            .order_by(MediaItemExportRow.id)
        )
    )
    return Transfer(
        account.user_id,
        account.export_id,
        _with_own_placements(db, target_id, moves),
        policy,
        denied,
    )


def stage_transfer(
    db: Session, transfer: Transfer, reviews: tuple[str, ...], now: datetime
) -> tuple[int, ...]:
    """Stage the switch in the caller's transaction and return every export it re-aims.

    Moved publications keep their UUID and advance their revision (MU-35). Reviews
    keep `original_author_id` and every scan keeps its initiator as provenance.
    """
    destination_id = transfer.destination_id
    switch_rows(db, transfer.moves, transfer.export_id)
    for move in transfer.moves:
        row = db.get(MediaItemExportRow, move.export_id)
        row.target_user_id = destination_id
        snapshot = json.loads(row.snapshot_json)
        snapshot["target_user_id"] = destination_id
        save_snapshot(row, snapshot)
    for review_id in reviews:
        review = db.get(SavedReviewRow, review_id)
        review.owner_id = destination_id
        review.revision += 1
        review.updated_at = now
    for export_id in transfer.policy_exports:
        db.get(MediaItemExportRow, export_id).intended_policy_owner_id = destination_id
    db.flush()
    followed = db.scalars(
        select(MediaItemExportRow.id).where(MediaItemExportRow.review_id.in_(reviews))
    ).all()
    affected = sorted(
        {*(move.export_id for move in transfer.moves), *transfer.policy_exports, *followed}
    )
    queue_regenerations(db, export_ids=affected)
    return tuple(affected)
