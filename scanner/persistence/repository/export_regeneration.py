"""Durable personal-export regeneration work, claimed through an indexed due queue."""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from stat import S_ISREG
from uuid import uuid4

from sqlalchemy.orm import Session

from scanner.db import MediaItemExportRow, SavedReviewRow
from scanner.persistence.rows.db_export_regenerations import (
    ExportRegenerationRow,
    RegenerationFailure,
    RegenerationState,
)
from scanner.persistence.rows.db_policies import ExportScope

from ..write_lock import write_lock
from .review_policy import owner_policy

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
CLAIMED = frozenset({RegenerationState.RUNNING, RegenerationState.WITHDRAWING})
RETRYABLE = frozenset(
    {RegenerationState.WITHDRAWING, RegenerationState.FAILED, RegenerationState.WITHDRAWN}
)


@dataclass(frozen=True)
class RegenerationJob:
    export_id: int
    publication_id: str
    publication_revision: int
    destination: str
    policy_owner_id: int | None
    policy_revision: int
    token: str


def save_snapshot(row, snapshot):
    row.snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))


def policy_owner(db: Session, row: MediaItemExportRow) -> int | None:
    """The live review owner; for a detached publication its intended owner, else its stamp."""
    if row.review_id is not None:
        return db.get(SavedReviewRow, row.review_id).owner_id
    if row.intended_policy_owner_id is not None:
        return row.intended_policy_owner_id
    return row.policy_owner_id


def policy_identity(db: Session, row: MediaItemExportRow) -> tuple[int | None, int]:
    """Resolve the policy owner and that owner's current policy revision."""
    owner_id = policy_owner(db, row)
    return owner_id, owner_policy(db, owner_id).policy_revision


def requeue(job: ExportRegenerationRow, owner_id: int | None, revision: int) -> None:
    """Make `job` due immediately for one policy, discarding any earlier claim or outcome."""
    job.state = RegenerationState.QUEUED
    job.due_at = 0.0
    job.attempts = 0
    job.last_attempt_at = None
    job.policy_owner_id = owner_id
    job.policy_revision = revision
    job.claim_token = None
    job.failure_reason = None


def requeue_withdrawn(db: Session, row: MediaItemExportRow) -> bool:
    """Queue a withdrawn, undeleted publication for its current policy; return whether it was.

    Called when its owned bytes are seen again, so an absence inference never becomes
    permanent (MU-34).
    """
    job = row.regeneration
    if row.content_bytes is not None or job is None or job.deleted:
        return False
    if job.state != RegenerationState.WITHDRAWN:
        return False
    requeue(job, *policy_identity(db, row))
    return True


def finish(job: ExportRegenerationRow, state: RegenerationState, reason=None) -> None:
    """Record a terminal outcome, which leaves the due index."""
    job.state, job.failure_reason = state, reason
    job.due_at = job.claim_token = None


def defer_withdrawal(job: ExportRegenerationRow) -> None:
    """Keep the published bytes owned and due again at the claim's backoff."""
    job.state = RegenerationState.WITHDRAWING
    job.failure_reason = RegenerationFailure.REGENERATION_FAILED
    job.claim_token = None


def _identity(job: ExportRegenerationRow) -> tuple[int | None, int]:
    return job.policy_owner_id, job.policy_revision


def align_regeneration(db: Session, row: MediaItemExportRow) -> bool:
    """Point `row`'s job at its current policy identity; return whether work was queued.

    A publication already stamped with that identity needs no work: its outdated job is
    dropped, or marked withdrawn again when the file was already withdrawn. A job that
    already targets the identity keeps its state, including a terminal outcome. A deleted
    record is never regenerated.
    """
    current = policy_identity(db, row)
    job = row.regeneration
    if job is not None and job.deleted:
        return False
    if (row.policy_owner_id, row.policy_revision) == current:
        if job is None or job.state == RegenerationState.WITHDRAWN or _identity(job) == current:
            return False
        if row.content_bytes is None:
            finish(job, RegenerationState.WITHDRAWN, RegenerationFailure.REGENERATION_FAILED)
        else:
            row.regeneration = None
        return False
    if job is not None and _identity(job) == current:
        return False
    if job is None:
        job = row.regeneration = ExportRegenerationRow()
    requeue(job, *current)
    return True


def queue_regenerations(db: Session, *, export_ids=None, review_ids=None) -> int:
    """Align one job per personal publication with its current policy, without committing."""
    with write_lock(db):
        query = db.query(MediaItemExportRow).filter(
            MediaItemExportRow.scope == ExportScope.PERSONAL,
            MediaItemExportRow.snapshot_json.is_not(None),
        )
        if export_ids is not None:
            query = query.filter(MediaItemExportRow.id.in_(export_ids))
        if review_ids is not None:
            query = query.filter(MediaItemExportRow.review_id.in_(review_ids))
        queued = sum(align_regeneration(db, row) for row in query)
        db.flush()
        return queued


def _next_due(db: Session, now: float, export_id: int | None):
    query = db.query(ExportRegenerationRow).filter(
        ExportRegenerationRow.due_at.is_not(None), ExportRegenerationRow.due_at <= now
    )
    if export_id is not None:
        query = query.filter(ExportRegenerationRow.export_id == export_id)
    return query.order_by(ExportRegenerationRow.due_at, ExportRegenerationRow.export_id).first()


def claim_regeneration(
    db: Session, *, export_id: int | None = None, now: float | None = None
) -> RegenerationJob | None:
    """Claim the earliest due job; a new token supersedes every previous delivery.

    An idle claim is one indexed lookup without locks. Due work is rechecked under them,
    and a job for an outdated policy identity is realigned before it spends an attempt.
    """
    from .export_recovery import publication_lock

    now = time.time() if now is None else now
    if _next_due(db, now, export_id) is None:
        db.rollback()
        return None
    with publication_lock(db), write_lock(db):
        while (job := _next_due(db, now, export_id)) is not None:
            row = job.export
            if _identity(job) != policy_identity(db, row):
                align_regeneration(db, row)
                db.flush()
                continue
            if job.state != RegenerationState.WITHDRAWING and job.attempts >= MAX_ATTEMPTS:
                finish(job, RegenerationState.FAILED, RegenerationFailure.RETRIES_EXHAUSTED)
                db.flush()
                continue
            job.attempts += 1
            if job.state == RegenerationState.QUEUED:
                job.state = RegenerationState.RUNNING
            job.claim_token = str(uuid4())
            job.last_attempt_at = now
            job.due_at = now + 5 * 2 ** min(job.attempts - 1, 6)
            claim = RegenerationJob(
                row.id,
                json.loads(row.snapshot_json)["publication_id"],
                row.publication_revision,
                row.destination_path,
                job.policy_owner_id,
                job.policy_revision,
                job.claim_token,
            )
            db.commit()
            return claim
        db.commit()
        return None


def reconcile_publications(db: Session, export_id: int) -> None:
    """Finish an interrupted write at the retained destination, or raise for manual recovery."""
    from scanner.formats.export_layout import parse_export_id

    from .export_publication import _check_destination, safe_destination
    from .export_recovery import _journal

    row = db.get(MediaItemExportRow, export_id)
    if row is None or row.scope == ExportScope.LEGACY or row.snapshot_json is None:
        return
    marker = _journal(Path(row.destination_path))
    if not marker.exists() and not marker.is_symlink():
        return
    snapshot = json.loads(row.snapshot_json)
    target = parse_export_id(row.target_export_id) if row.target_export_id else None
    destination = safe_destination(
        Path(snapshot["media_path"]), row.format, target, require_media=False
    )
    _check_destination(db, row, destination)


def _owned_bytes_returned(row: MediaItemExportRow) -> bool:
    """Whether a withdrawn publication's last written bytes are back at its retained path."""
    from .export_recovery import digest, owned_digest

    job = row.regeneration
    if row.content_bytes is not None or job is None or job.state != RegenerationState.WITHDRAWN:
        return False
    evidence = owned_digest(row)
    path = Path(row.destination_path)
    try:
        if evidence is None or not S_ISREG(os.lstat(path).st_mode):
            return False
        return digest(path.read_bytes()) == evidence
    except OSError:
        return False


def _needs_attention(db: Session, row: MediaItemExportRow) -> bool:
    from .export_recovery import _journal, deleted

    if _owned_bytes_returned(row):
        return True
    if (
        row.scope == ExportScope.PERSONAL
        and not deleted(row)
        and policy_identity(db, row) != (row.policy_owner_id, row.policy_revision)
    ):
        return True
    marker = _journal(Path(row.destination_path))
    try:
        return marker.exists() or marker.is_symlink()
    except OSError:
        return True


def sweep_regenerations(db: Session) -> None:
    """Queue stale stamps, journals, and returned owned bytes, rechecking each under locks.

    Candidates are found without locks. A withdrawn publication whose owned bytes
    reappear is queued for its current policy: the claim regenerates it when the media
    resolves and withdraws it again otherwise. A deleted record's returned owned bytes
    are removed here instead, because it is never regenerated.
    A publication whose sidecar cannot be inspected or recovered is logged and still
    queued, so its own claim decides; it never blocks other publications.
    Interrupted account links and transfers are converged first, since they own files
    at two paths.
    """
    from scanner.security.account_links.transition import recover

    from . import account_transfer_files
    from .export_deletion import remove_returned_file
    from .export_recovery import publication_lock

    db.rollback()
    with publication_lock(db):
        recover(db, datetime.now(UTC))
        account_transfer_files.recover(db)

    candidates = [
        row.id
        for row in db.query(MediaItemExportRow).filter(
            MediaItemExportRow.scope != ExportScope.LEGACY,
            MediaItemExportRow.snapshot_json.is_not(None),
        )
        if _needs_attention(db, row)
    ]
    db.rollback()
    for export_id in candidates:
        with publication_lock(db), write_lock(db):
            try:
                reconcile_publications(db, export_id)
            except (OSError, ValueError):
                logger.warning("Export %s requires manual sidecar recovery", export_id)
            queue_regenerations(db, export_ids=[export_id])
            row = db.get(MediaItemExportRow, export_id)
            if row is not None and _owned_bytes_returned(row):
                if not row.regeneration.deleted:
                    requeue_withdrawn(db, row)
                else:
                    try:
                        remove_returned_file(db, row)
                    except (OSError, ValueError):
                        logger.warning("Deleted export %s keeps unremoved bytes", export_id)
            db.commit()


def current_job(db: Session, job: RegenerationJob):
    """Decide against fresh queue, publication, and policy state under the writer lock."""
    db.expire_all()
    row = db.get(MediaItemExportRow, job.export_id)
    if row is None or row.scope != ExportScope.PERSONAL or row.snapshot_json is None:
        return None
    snapshot = json.loads(row.snapshot_json)
    state = row.regeneration
    if (
        state is None
        or snapshot["publication_id"] != job.publication_id
        or row.publication_revision != job.publication_revision
        or row.destination_path != job.destination
        or state.claim_token != job.token
        or state.state not in CLAIMED
        or policy_identity(db, row) != (job.policy_owner_id, job.policy_revision)
    ):
        return None
    return row, snapshot


def retry_regeneration(db: Session, export_id: int, publication_id: str) -> bool:
    """Stage an explicit retry of a retained failed publication without restoring its file."""
    from .export_recovery import publication_lock

    with publication_lock(db), write_lock(db):
        row = db.get(MediaItemExportRow, export_id)
        if row is None or row.snapshot_json is None or row.regeneration is None:
            return False
        job = row.regeneration
        if (
            json.loads(row.snapshot_json)["publication_id"] != publication_id
            or job.state not in RETRYABLE
            or job.deleted
        ):
            return False
        requeue(job, *policy_identity(db, row))
        db.commit()
        return True
