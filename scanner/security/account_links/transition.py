"""The durable identity and export transition behind one account link.

States move `writing` -> `removing` -> `completed`, or `writing` -> `rolled_back`.
`begin` commits the planned moves before any file is written. `write_files` places
the new files, committing each staged file's identity before placing it. `switch`
changes the identity, media basis, and publications in one transaction and
enters `removing`. `finish` removes the old files it recognizes. Recovery decides
from the stored state alone: an open `writing` transition never switched the
identity, so the new files it placed are discarded and the old ones stay; a
`removing` one did, so its old files are removed. Every removal follows the shared
removal rule (`files.removable`); a file it keeps stays while the record closes. Every
step recognizes files by recorded evidence, so it may run any number of times. Callers
hold the export publication lock from `begin` through `finish` and during recovery.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from scanner.db import MediaItemExportRow, UserProviderIdentityRow, UserRow
from scanner.integrations.jellyfin_user_snapshot import JellyfinUserSnapshot
from scanner.persistence.repository import export_identity_moves as files
from scanner.persistence.repository.export_publication import ExportConflict
from scanner.persistence.repository.export_regeneration import requeue_withdrawn
from scanner.persistence.rows.db_account_links import (
    OPEN_STATES,
    AccountLinkExportMoveRow,
    AccountLinkTransitionRow,
    MoveOutcome,
    RollbackReason,
    TransitionState,
)
from scanner.persistence.write_lock import write_lock
from scanner.security import account_repository as accounts
from scanner.security.access.identity import DisableCause, UserId
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.audit.actions import ActorKind, AuditAction
from scanner.security.audit.store import stage_event
from scanner.security.jellyfin_accounts import blocks, store
from scanner.security.jellyfin_accounts.reads import ReadStart

from .plan import LinkPlan, linkable
from .proofs import ProofBinding, read_binding

logger = logging.getLogger(__name__)


class SwitchRefusal(StrEnum):
    """Every reason `switch` refuses; each is also an `account.link` audit reason."""

    PREVIEW_STALE = "link_preview_stale"
    PROOF_STALE = "link_proof_stale"
    IDENTITY_IN_USE = "jellyfin_identity_in_use"
    USER_BLOCKED = "jellyfin_user_blocked"
    ACCOUNT_DISABLED = "account_disabled"
    JELLYFIN_UNAVAILABLE = "jellyfin_unavailable"


class LinkRefused(Exception):
    def __init__(self, reason: SwitchRefusal, detail: str) -> None:
        super().__init__(reason.value)
        self.code, self.detail = reason.value, detail
        self.retry = reason is SwitchRefusal.JELLYFIN_UNAVAILABLE
        self.status = 503 if self.retry else 409


def begin(db: Session, plan: LinkPlan, actor_id: int, now: datetime) -> AccountLinkTransitionRow:
    """Stage an open `writing` transition and its moves; the caller commits under its locks."""
    stored = store.stored_time(now)
    row = AccountLinkTransitionRow(
        user_id=plan.user_id,
        actor_id=actor_id,
        kind=plan.kind.value,
        state=TransitionState.WRITING.value,
        from_server_id=plan.current.server_id if plan.current else None,
        from_provider_user_id=plan.current.user_id.value if plan.current else None,
        to_server_id=plan.after.server_id,
        to_provider_user_id=plan.after.user_id.value,
        from_export_id=plan.from_export_id.value,
        created_at=stored,
        updated_at=stored,
    )
    row.moves = [
        AccountLinkExportMoveRow(
            export_id=move.export_id,
            old_path=str(move.old_path),
            new_path=str(move.new_path),
            old_sha256=move.old_sha256,
            new_sha256=move.new_sha256,
        )
        for move in plan.moves
    ]
    db.add(row)
    db.flush()
    return row


def write_files(db: Session, transition_id: int, plan: LinkPlan) -> None:
    """Write every new file; a failure leaves the rest for `roll_back` to discard.

    Each staged file's identity commits on its move before the file is placed, so
    rollback can tell the file Editarr placed from one that appeared at the new path,
    even after a crash right after placement.
    """
    for planned in plan.moves:
        staged = files.write_new(planned, plan.to_export_id)
        if planned.content is None:
            continue
        with write_lock(db):
            move = db.scalars(
                select(AccountLinkExportMoveRow).filter_by(
                    transition_id=transition_id, new_path=str(planned.new_path)
                )
            ).one()
            move.new_file_id = staged
            db.commit()
        files.place_new(planned.new_path)


def switch(
    db: Session,
    transition_id: int,
    plan: LinkPlan,
    snapshot: JellyfinUserSnapshot,
    read: ReadStart,
    avatar: store.AvatarChange | None,
    binding: ProofBinding,
) -> store.SyncResult:
    """Stage the identity change and publication moves under the caller's write lock.

    An account disabled for having no link becomes active. Raises `LinkRefused` when
    the proof's `binding` (the target's permission revision and identities) no longer
    holds, when the account or the identity changed since `begin`, when the Jellyfin
    user was blocked after the preview, or when a later Jellyfin read already applied
    to the account.
    """
    transition = db.get(AccountLinkTransitionRow, transition_id)
    if transition is None or transition.state != TransitionState.WRITING.value:
        raise LinkRefused(SwitchRefusal.PREVIEW_STALE, "The link was interrupted; prove it again")
    if read_binding(db, plan.user_id, plan.after) != binding:
        raise LinkRefused(SwitchRefusal.PROOF_STALE, "The account changed; prove it again")
    holder = store.find_identity(db, plan.after)
    if holder is not None:
        raise LinkRefused(SwitchRefusal.IDENTITY_IN_USE, "Another account has this identity")
    if blocks.is_blocked(db, plan.after):
        raise LinkRefused(SwitchRefusal.USER_BLOCKED, "This Jellyfin user is blocked")
    status, cause = db.execute(
        select(UserRow.status, UserRow.disable_cause).where(UserRow.id == plan.user_id)
    ).one()
    if not linkable(status, cause):
        raise LinkRefused(SwitchRefusal.ACCOUNT_DISABLED, "The account is disabled")
    identity = db.get(UserProviderIdentityRow, plan.user_id)
    if identity is None:
        db.add(
            UserProviderIdentityRow(
                user_id=plan.user_id,
                server_id=plan.after.server_id,
                provider_user_id=plan.after.user_id.value,
                link_state="verified",
                created_at=store.stored_time(read.at),
                updated_at=store.stored_time(read.at),
            )
        )
    else:
        identity.server_id = plan.after.server_id
        identity.provider_user_id = plan.after.user_id.value
        identity.observed_generation = None
        identity.last_verified_at = None
    db.flush()
    if cause == DisableCause.UNLINKED.value:
        accounts.set_disable_cause(db, plan.user_id, None, now=read.at)
    result = store.observe(db, plan.user_id, snapshot, read, avatar, identity=plan.after)
    if result is None:
        raise LinkRefused(SwitchRefusal.JELLYFIN_UNAVAILABLE, "A newer Jellyfin read intervened")
    try:
        files.switch_rows(db, plan.moves, plan.to_export_id)
    except ExportConflict:
        raise LinkRefused(SwitchRefusal.PREVIEW_STALE, "A personal export changed") from None
    db.execute(
        update(UserRow)
        .where(UserRow.id == plan.user_id)
        .values(permission_revision=UserRow.permission_revision + 1)
    )
    transition.state = TransitionState.REMOVING.value
    transition.updated_at = store.stored_time(read.at)
    db.flush()
    return result


def _settle(db: Session, move: AccountLinkExportMoveRow, outcome: str) -> None:
    """Record how an old path settled in the caller's transaction."""
    move.outcome = outcome


def remove_old_owned(db: Session, move) -> str:
    """Remove `move`'s old file when Editarr owns it and return the outcome.

    `move` is a link or account-transfer move row; both carry the same evidence. An old
    file the shared removal rule keeps (`files.removable`) settles `kept`, so
    reconciliation keeps watching it.

    Owned bytes of a withdrawn record are the only evidence that its media is back, so
    the requeue they earn commits before the unlink destroys them. A crash or error
    after that commit leaves the job queued; one before it leaves the bytes for the next
    pass. The requeue commits with `move.requeued`, so bytes that cannot be removed earn
    it once; the mark clears in the caller's transaction once no owned bytes remain. Call inside
    `write_lock`; committing a requeue ends that lock.
    """
    old_path, old_sha256 = Path(move.old_path), move.old_sha256
    if not files.removable(db, move, old_path):
        return MoveOutcome.KEPT
    row = db.get(MediaItemExportRow, move.export_id) if move.export_id is not None else None
    held = files.holds_owned(old_path, old_sha256)
    if held and not move.requeued and row is not None and requeue_withdrawn(db, row):
        move.requeued = True
        db.commit()
    outcome = files.remove_old(old_path, old_sha256)
    move.requeued = False
    return outcome


def finish(db: Session, transition_id: int, now: datetime) -> bool:
    """Remove the old files of a `removing` transition and complete it when all are settled.

    Each move settles in its own commit. Returns whether it completed. A filesystem
    error other than absence leaves the transition open for the next recovery.
    """
    with write_lock(db):
        transition = db.get(AccountLinkTransitionRow, transition_id)
        if transition is None or transition.state != TransitionState.REMOVING.value:
            db.rollback()
            return False
        unsettled = [move.id for move in transition.moves if move.outcome is None]
        db.rollback()
    for move_id in unsettled:
        with write_lock(db):
            move = db.get(AccountLinkExportMoveRow, move_id)
            try:
                _settle(db, move, remove_old_owned(db, move))
            except OSError:
                logger.warning("Account link %s keeps an old export for retry", transition_id)
                db.rollback()
                continue
            db.commit()
    with write_lock(db):
        transition = db.get(AccountLinkTransitionRow, transition_id)
        settled = all(move.outcome is not None for move in transition.moves)
        if settled and transition.state == TransitionState.REMOVING.value:
            transition.state = TransitionState.COMPLETED.value
        transition.updated_at = store.stored_time(now)
        db.commit()
    return settled


def roll_back(db: Session, transition_id: int, reason: RollbackReason, now: datetime) -> None:
    """Discard the new files of a `writing` transition and record why; old files stay."""
    with write_lock(db):
        transition = db.get(AccountLinkTransitionRow, transition_id)
        if transition is None or transition.state != TransitionState.WRITING.value:
            db.rollback()
            return
        for move in transition.moves:
            files.discard_moved(db, move)
        transition.state = TransitionState.ROLLED_BACK.value
        transition.failure_reason = reason.value
        transition.updated_at = store.stored_time(now)
        db.commit()


def committed_state(db: Session, transition_id: int) -> str | None:
    """Read a transition's state from a fresh connection after an uncertain commit."""
    db.rollback()
    with db.get_bind().connect() as connection:
        return connection.execute(
            select(AccountLinkTransitionRow.state).where(
                AccountLinkTransitionRow.id == transition_id
            )
        ).scalar_one_or_none()


def _recovery_event(transition: AccountLinkTransitionRow, now: datetime, state: str) -> AuditEvent:
    return AuditEvent(
        now,
        None,
        None,
        AuditAction.ACCOUNT_LINK,
        AuditOutcome.SUCCEEDED,
        target_user=UserId(transition.user_id),
        details={"step": "recovery", "kind": transition.kind, "state": state},
    )


def _settled_moves(db: Session, user_id: int | None) -> list[tuple[int, str]]:
    moves = AccountLinkExportMoveRow
    query = (
        db.query(moves.id, moves.old_path)
        .join(AccountLinkTransitionRow)
        .filter(
            AccountLinkTransitionRow.state == TransitionState.COMPLETED.value,
            moves.outcome.in_([MoveOutcome.ABSENT, MoveOutcome.KEPT]),
            moves.old_sha256.is_not(None),
        )
    )
    if user_id is not None:
        query = query.filter(AccountLinkTransitionRow.user_id == user_id)
    return [tuple(row) for row in query.order_by(moves.id)]


def reconcile_old_paths(db: Session, now: datetime, *, user_id: int | None = None) -> int:
    """Remove owned bytes that returned to an old path after its move settled; return how many.

    A move settled `absent` or `kept` keeps its old path and digest as ownership evidence:
    one ENOENT is not permanent removal (MU-34). A regular file there with exactly those
    bytes is an export under an ID the account no longer has, whether the publication
    still serves, was withdrawn, or was deleted, so it is removed once the shared removal
    rule allows it (`files.removable`). A withdrawn record is
    also queued to serve again (`remove_old_owned`). A path that a publication records again
    belongs to it and is left alone. Call under the export publication lock.
    """
    candidates = _settled_moves(db, user_id)
    db.rollback()
    removed = 0
    for move_id, old_path in candidates:
        with write_lock(db):
            move = db.get(AccountLinkExportMoveRow, move_id)
            if files.recorded(db, old_path) or move is None or move.outcome == MoveOutcome.REMOVED:
                db.rollback()
                continue
            try:
                outcome = remove_old_owned(db, move)
            except OSError:
                logger.warning("Account link move %s keeps returned old bytes for retry", move_id)
                db.rollback()
                continue
            if outcome == MoveOutcome.REMOVED:
                _settle(db, move, outcome)
                move.transition.updated_at = store.stored_time(now)
                removed += 1
            db.commit()
    return removed


def recover(db: Session, now: datetime, *, user_id: int | None = None) -> int:
    """Converge every open transition (or one account's) and return how many were open.

    Settled moves whose owned old bytes returned are reconciled afterwards. Call under
    the export publication lock, never inside a write transaction.
    """
    query = db.query(AccountLinkTransitionRow.id, AccountLinkTransitionRow.state).filter(
        AccountLinkTransitionRow.state.in_([state.value for state in OPEN_STATES])
    )
    if user_id is not None:
        query = query.filter(AccountLinkTransitionRow.user_id == user_id)
    open_transitions = query.order_by(AccountLinkTransitionRow.id).all()
    db.rollback()
    for transition_id, state in open_transitions:
        try:
            if state == TransitionState.WRITING.value:
                roll_back(db, transition_id, RollbackReason.INTERRUPTED, now)
            else:
                finish(db, transition_id, now)
        except OSError:
            logger.warning("Account link %s stays open for the next recovery", transition_id)
            continue
        with write_lock(db):
            transition = db.get(AccountLinkTransitionRow, transition_id)
            if transition is not None and transition.state not in {s.value for s in OPEN_STATES}:
                stage_event(
                    db,
                    _recovery_event(transition, now, transition.state),
                    actor_kind=ActorKind.SYSTEM,
                )
            db.commit()
    reconcile_old_paths(db, now, user_id=user_id)
    return len(open_transitions)
