"""One account's deletion preview, confirmed disposition, transfer, and replay (MU-38).

Every mode needs an interactive session and the caller's password in the request
(`reauthentication`). A preview needs only the session. Refusals of a confirmed
deletion are audited once per attempt; previews are reads and are not audited.

Before any mode is previewed or applied, an earlier transfer the account left
unfinished is converged under the export publication lock (`_settle`); moves it
cannot settle yet block every mode with a count. Confirmed dispositions hold that
lock through their commit. Previews take it only to settle, because the confirmed
attempt recomputes the impact under the lock and refuses any difference.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from errno import ELOOP, ENOENT, ENOTDIR

from sqlalchemy.exc import OperationalError

from scanner.modeling.model_account_deletion import (
    AccountDeletionPreview,
    AccountDeletionResult,
    AccountDisposition,
)
from scanner.persistence.busy import is_busy
from scanner.persistence.repository import account_transfer_files as transfer_files
from scanner.persistence.repository.account_consent import preview
from scanner.persistence.repository.account_disposition import (
    DispositionRequest,
    apply_disposition,
    disposition_gate,
    stored_disposition,
    stored_result,
)
from scanner.persistence.repository.account_impact import (
    AccountDispositionConflict,
    AccountImpact,
    Disposition,
)
from scanner.persistence.repository.export_publication import ExportConflict
from scanner.persistence.repository.export_recovery import ExportRecoveryRequired, publication_lock
from scanner.persistence.repository.review_access import ReviewAccess
from scanner.persistence.repository.review_disposition import DispositionConflict, FileChoice
from scanner.persistence.rows.db_account_dispositions import AccountDispositionRow
from scanner.repository import ReviewRevisionConflict
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.targets import AccountRef
from scanner.security.audit.actions import ACCOUNT_DELETE_REASONS, AuditAction

from . import account_operations as operations
from .users_access import Caller, Refusal

logger = logging.getLogger(__name__)
AUDITED = AuditAction.ACCOUNT_DELETE
CAPABILITY = C.ACCOUNTS_DELETE_OTHER
_UNREACHABLE = frozenset({ENOENT, ENOTDIR, ELOOP})


def _review_access(caller: Caller) -> ReviewAccess:
    return ReviewAccess(caller.db, caller.access, caller.access.session_id)


def _refusal(error: Exception) -> tuple[Refusal, str] | None:
    """The response and audit reason for an expected refusal, or None for a defect."""
    if isinstance(error, AccountDispositionConflict):
        return Refusal(error.status, error.code, str(error)), error.code
    if isinstance(error, DispositionConflict):
        status = 422 if error.code == "review_deletion_choice_required" else 409
        return Refusal(status, error.code, str(error)), error.code
    if isinstance(error, ReviewRevisionConflict):
        code = "stale_account_deletion"
        return Refusal(409, code, "A review changed; reload the deletion"), code
    if isinstance(error, ExportConflict):
        reason = error.code if error.code in ACCOUNT_DELETE_REASONS else "export_conflict"
        return Refusal(409, error.code, str(error)), reason
    if isinstance(error, ExportRecoveryRequired):
        code = "export_recovery_required"
        return Refusal(409, code, "Export recovery is required"), code
    if isinstance(error, OSError):
        if error.errno in _UNREACHABLE:
            detail = "An export folder cannot be reached; restore it and retry"
            return Refusal(409, "export_destination_unavailable", detail), (
                "export_destination_unavailable"
            )
        detail = "An export file could not be changed; check its folder and retry"
        return Refusal(409, "export_io_failed", detail), "export_io_failed"
    if isinstance(error, OperationalError) and is_busy(error):
        detail = "The database is busy; retry shortly"
        return Refusal(503, "database_busy", detail, True), "database_busy"
    return None


def _refuse(caller: Caller, ask: DispositionRequest, user_id: int, reason: str) -> None:
    if not ask.preview:
        caller.refuse(AUDITED, user_id, reason)


@contextmanager
def _audited(caller: Caller, ask: DispositionRequest, user_id: int) -> Iterator[None]:
    """Turn an expected failure into its refusal, audited once for a confirmed attempt."""
    try:
        yield
    except AccessDenied as denial:
        if not ask.preview:
            caller.refuse(AUDITED, user_id, denial.code.value, capability=CAPABILITY)
        raise
    except Refusal:
        raise
    except Exception as error:
        mapped = _refusal(error)
        if mapped is None:
            raise
        refusal, reason = mapped
        _refuse(caller, ask, user_id, reason)
        raise refusal from error


@contextmanager
def _gate(
    caller: Caller,
    review: ReviewAccess,
    target: AccountRef,
    ask: DispositionRequest,
    unsettled: int = 0,
) -> Iterator[AccountImpact]:
    with _audited(caller, ask, target.user_id):
        with disposition_gate(review, caller.access, target, ask, unsettled) as impact:
            yield impact


def _settle(caller: Caller, ask: DispositionRequest, user_id: int) -> int:
    """Converge the account's unfinished transfer; return how many moves stay open.

    Call under the publication lock.
    """
    with _audited(caller, ask, user_id):
        transfer_files.recover(caller.db, user_id=user_id)
        return transfer_files.unsettled(caller.db, user_id)


def _confirmed(review: ReviewAccess, impact: AccountImpact, confirmation: str) -> dict:
    consent = preview(review, impact)
    if consent["confirmation"] != confirmation:
        raise AccountDispositionConflict(
            409, "stale_account_deletion", "The account changed; review the deletion again"
        )
    if impact.blockers:
        raise AccountDispositionConflict(
            409, "account_deletion_blocked", "Resolve the listed blockers first"
        )
    return consent


def preview_deletion(
    caller: Caller, user_id: int, mode: Disposition, destination_id: int | None
) -> AccountDeletionPreview:
    target = caller.other(user_id, CAPABILITY)
    decision = decide(caller.access, CAPABILITY, target)
    if not isinstance(decision, Allowed) and (
        decision.code is not AccessErrorCode.REAUTHENTICATION_REQUIRED
    ):
        raise AccessDenied(decision.code)
    if (mode is Disposition.TRANSFER) != (destination_id is not None):
        raise Refusal(422, "transfer_destination_required", "Name a destination for a transfer")
    ask = DispositionRequest(mode, destination_id, preview=True)
    review = _review_access(caller)
    unsettled = 0
    try:
        if transfer_files.unsettled(caller.db, user_id):
            with publication_lock(caller.db):
                unsettled = _settle(caller, ask, user_id)
        with _gate(caller, review, target, ask, unsettled) as impact:
            return AccountDeletionPreview(**preview(review, impact))
    finally:
        caller.db.rollback()


def _replay(caller: Caller, row: AccountDispositionRow, body: AccountDisposition):
    """Return a completed deletion's result to the actor who confirmed exactly it."""
    stored = stored_result(row)
    blocked = body.jellyfin_user == "block" and stored["identity_released"]
    if (
        row.actor_id != int(caller.own.user_id)
        or row.mode != body.mode
        or row.confirmation != body.confirmation
        or row.destination_id != body.destination_id
        or blocked != stored.get("jellyfin_blocked", False)
    ):
        raise AccessDenied(AccessErrorCode.NOT_FOUND)
    return AccountDeletionResult(**stored, replayed=True)


def _apply(caller, review, target, ask, body, choices, unsettled=0) -> dict:
    with _gate(caller, review, target, ask, unsettled) as impact:
        consent = _confirmed(review, impact, body.confirmation)
        return apply_disposition(
            caller.db,
            caller.access,
            impact,
            consent,
            choices,
            now=caller.now,
            stage_audit=lambda details: caller.stage(AUDITED, target.user_id, details),
            block=body.jellyfin_user == "block",
        )


def _retain_or_delete(caller, review, target, ask, body, choices) -> dict:
    with publication_lock(caller.db):
        unsettled = _settle(caller, ask, target.user_id)
        return _apply(caller, review, target, ask, body, choices, unsettled)


def _abandon(db, user_id: int) -> None:
    """Undo a transfer that did not switch; recovery retries what cannot be undone now."""
    db.rollback()
    try:
        transfer_files.roll_back(db, user_id)
    except (OSError, OperationalError):
        db.rollback()
        logger.warning("Account transfer rollback waits for the next recovery")


def _write_refusal(caller, ask, user_id: int, error: Exception) -> Refusal:
    """The audited refusal of a transfer whose new files could not be written."""
    if isinstance(error, (OSError, ValueError)):
        code = error.code if isinstance(error, ExportConflict) else "export_write_failed"
        reason = code if code in ACCOUNT_DELETE_REASONS else "export_conflict"
        refusal = Refusal(409, code, "A moved export could not be written; nothing changed")
    else:
        mapped = _refusal(error)
        if mapped is None:
            raise error
        refusal, reason = mapped
    _refuse(caller, ask, user_id, reason)
    return refusal


def _transfer(caller, review, target, ask, body, choices) -> dict:
    """Write moved files, switch the account in one commit, then remove old files.

    The publication lock is held throughout, and each phase recomputes the
    confirmed consent under the writer lock. Any failure before the switch commits
    discards the new files; a crash is converged by `account_transfer_files.recover`.
    """
    db, user_id = caller.db, target.user_id
    with publication_lock(db):
        unsettled = _settle(caller, ask, user_id)
        with _gate(caller, review, target, ask, unsettled) as impact:
            _confirmed(review, impact, body.confirmation)
            transfer_files.begin(db, user_id, body.confirmation, impact.transfer.moves)
            db.commit()
        transfer = impact.transfer
        try:
            transfer_files.write(db, user_id, transfer.moves, transfer.export_id)
        except Exception as error:
            _abandon(db, user_id)
            raise _write_refusal(caller, ask, user_id, error) from error
        try:
            result = _apply(caller, review, target, ask, body, choices)
        except BaseException:
            _abandon(db, user_id)
            raise
        _finish(db, user_id)
    return result


def _finish(db, user_id: int) -> None:
    """Remove the old files of a committed switch; a busy database leaves them to recovery.

    The transfer has committed, so the response reports it as applied either way.
    """
    try:
        transfer_files.finish(db, user_id)
    except OperationalError as error:
        if not is_busy(error):
            raise
        db.rollback()
        logger.warning("Account transfer leaves old exports for the next recovery")


def delete_account(
    caller: Caller, proofs: operations.Proofs, user_id: int, body: AccountDisposition
) -> AccountDeletionResult:
    """Apply one confirmed disposition, or replay its stored result."""
    stored = stored_disposition(caller.db, user_id)
    if stored is not None:
        return _replay(caller, stored, body)
    target = caller.other(user_id, CAPABILITY, audited=AUDITED)
    fresh = operations.authorized(caller, proofs, CAPABILITY, target, AUDITED)
    ask = DispositionRequest(Disposition(body.mode), body.destination_id, fresh)
    review = _review_access(caller)
    choices = {file.export_id: FileChoice(file.choice) for file in body.files}
    apply = _transfer if ask.mode is Disposition.TRANSFER else _retain_or_delete
    return AccountDeletionResult(
        **apply(caller, review, target, ask, body, choices), replayed=False
    )
