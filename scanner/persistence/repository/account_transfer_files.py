"""The durable file half of an account transfer, built on MU-35's move machinery.

`begin` stages one `account_transfer_moves` row per planned move, bound to the
confirmation the actor confirmed; the caller commits it before any file is
written. `write` stages each new file, commits its identity, then places it. The
account switch commits the publications at their new paths together with the
account's disposition record. `finish` then removes the old files Editarr
recognizes. Recovery decides from stored rows alone: rows whose confirmation is
the account's committed transfer switched, so `finish` removes their old files;
any other rows (no disposition, or a retain, delete, or different transfer that
committed later) never switched, so `roll_back` discards the new files it can
recognize and drops the rows. Every removal follows the shared removal rule
(`export_identity_moves.removable`): a file it keeps stays while the row closes, so
a restored database that disagrees with the disk never costs the only usable copy.
Callers hold the export publication lock.
"""

import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scanner.formats.export_layout import ExportId
from scanner.persistence.rows.db_account_dispositions import (
    AccountDispositionRow,
    AccountTransferMoveRow,
)
from scanner.persistence.rows.db_account_links import MoveOutcome
from scanner.security.account_links.transition import remove_old_owned

from ..write_lock import write_lock
from . import export_identity_moves as files
from .export_identity_moves import PlannedMove

logger = logging.getLogger(__name__)
Moves = AccountTransferMoveRow


def begin(db: Session, user_id: int, confirmation: str, moves: tuple[PlannedMove, ...]) -> None:
    """Stage the planned moves of the transfer `confirmation` in the caller's transaction."""
    db.add_all(
        Moves(
            user_id=user_id,
            confirmation=confirmation,
            export_id=move.export_id,
            old_path=str(move.old_path),
            new_path=str(move.new_path),
            old_sha256=move.old_sha256,
            new_sha256=move.new_sha256,
        )
        for move in moves
    )
    db.flush()


def unsettled(db: Session, user_id: int) -> int:
    """How many of the account's moves recovery has not yet settled."""
    return db.scalar(
        select(func.count(Moves.id)).filter_by(user_id=user_id).where(Moves.outcome.is_(None))
    )


def placed(db: Session, user_id: int, new_path: Path) -> bool:
    """Whether `new_path` holds the file this account's open transfer placed there."""
    row = db.scalar(select(Moves).filter_by(user_id=user_id, new_path=str(new_path)))
    return row is not None and files.holds_placed(new_path, row.new_sha256, row.new_file_id)


def write(db: Session, user_id: int, moves: tuple[PlannedMove, ...], new_id: ExportId) -> None:
    """Write every new file, committing each staged identity before placement."""
    for planned in moves:
        staged = files.write_new(planned, new_id)
        if planned.content is None:
            continue
        with write_lock(db):
            row = db.scalars(
                select(Moves).filter_by(user_id=user_id, new_path=str(planned.new_path))
            ).one()
            row.new_file_id = staged
            db.commit()
        files.place_new(planned.new_path)


def _committed_transfer(db: Session, user_id: int) -> str | None:
    """The confirmation of the account's committed transfer, or None."""
    return db.scalar(
        select(AccountDispositionRow.confirmation).filter_by(user_id=user_id, mode="transfer")
    )


def _abandoned(db: Session, user_id: int):
    """The account's moves that do not belong to its committed transfer."""
    query = select(Moves).filter_by(user_id=user_id)
    committed = _committed_transfer(db, user_id)
    if committed is not None:
        query = query.where(Moves.confirmation != committed)
    return query


def roll_back(db: Session, user_id: int) -> None:
    """Discard the new files of every transfer of the account that never switched, then
    their rows. Rows of the committed transfer stay for `finish`."""
    with write_lock(db):
        rows = db.scalars(_abandoned(db, user_id)).all()
        for row in rows:
            files.discard_moved(db, row)
            db.delete(row)
        db.commit()


def finish(db: Session, user_id: int) -> bool:
    """Remove the old files of a switched transfer; return whether every move settled.

    Each move settles in its own commit. An old file the shared removal rule keeps
    settles as `kept`. A filesystem error other than absence leaves that move for
    the next recovery.
    """
    with write_lock(db):
        committed = _committed_transfer(db, user_id)
        pending = db.scalars(
            select(Moves.id)
            .filter_by(user_id=user_id, outcome=None, confirmation=committed)
            .order_by(Moves.id)
        ).all()
        db.rollback()
    if committed is None:
        return False
    settled = True
    for move_id in pending:
        with write_lock(db):
            move = db.get(Moves, move_id)
            if files.recorded(db, move.old_path):
                move.outcome = MoveOutcome.KEPT
                db.commit()
                continue
            try:
                move.outcome = remove_old_owned(db, move)
            except OSError:
                logger.warning("Account transfer keeps an old export for retry")
                db.rollback()
                settled = False
                continue
            db.commit()
    return settled


def _returned(db: Session, user_id: int | None) -> list[int]:
    query = (
        select(Moves.id)
        .join(
            AccountDispositionRow,
            (AccountDispositionRow.user_id == Moves.user_id)
            & (AccountDispositionRow.mode == "transfer")
            & (AccountDispositionRow.confirmation == Moves.confirmation),
        )
        .where(
            Moves.outcome.in_([MoveOutcome.ABSENT, MoveOutcome.KEPT]),
            Moves.old_sha256.is_not(None),
        )
    )
    if user_id is not None:
        query = query.where(Moves.user_id == user_id)
    return list(db.scalars(query.order_by(Moves.id)))


def _reconcile_returned(db: Session, user_id: int | None) -> None:
    """Remove owned bytes that came back to a settled old path (MU-34: absence is not final)."""
    candidates = _returned(db, user_id)
    db.rollback()
    for move_id in candidates:
        with write_lock(db):
            move = db.get(Moves, move_id)
            if files.recorded(db, move.old_path):
                db.rollback()
                continue
            try:
                outcome = remove_old_owned(db, move)
            except OSError:
                db.rollback()
                continue
            if outcome == MoveOutcome.REMOVED:
                move.outcome = outcome
            db.commit()


def recover(db: Session, *, user_id: int | None = None) -> int:
    """Converge every unfinished transfer (or one account's); return how many were open.

    Call under the export publication lock, never inside a write transaction.
    """
    query = select(Moves.user_id).where(Moves.outcome.is_(None)).distinct()
    if user_id is not None:
        query = query.where(Moves.user_id == user_id)
    accounts = sorted(db.scalars(query))
    db.rollback()
    for account in accounts:
        try:
            roll_back(db, account)
            finish(db, account)
        except OSError:
            logger.warning("Account transfer stays open for the next recovery")
        db.rollback()
    _reconcile_returned(db, user_id)
    return len(accounts)
