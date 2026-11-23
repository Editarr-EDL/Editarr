"""Run revision-pinned export jobs through the canonical recoverable publisher."""

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import OperationalError

from scanner.formats.export_layout import parse_export_id
from scanner.persistence.repository.export_publication import (
    POLICY_CHANNEL_EVALUATION,
    ExportConflict,
    _check_destination,
    prepare_export_directory,
    resolve_directory,
    safe_destination,
)
from scanner.persistence.repository.export_recovery import (
    OWNED_DIGEST,
    ExportRecoveryRequired,
    committed_export_content,
    owned_digest,
    publication_lock,
    recoverable_sidecar,
    retained_path_absent,
)
from scanner.persistence.repository.export_regeneration import (
    RegenerationJob,
    claim_regeneration,
    current_job,
    defer_withdrawal,
    finish,
    save_snapshot,
    sweep_regenerations,
)
from scanner.persistence.repository.export_retained import prepare_retained_export
from scanner.persistence.repository.media_identity_store import get_version, review_version_id
from scanner.persistence.repository.review_version_evidence import file_digest_matches
from scanner.persistence.rows.db_export_regenerations import (
    RegenerationFailure,
    RegenerationState,
)
from scanner.persistence.write_lock import write_lock
from scanner.security.access.identity import UserId
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.audit.actions import ActorKind, AuditAction
from scanner.security.audit.store import stage_event

from .maintenance import MaintenanceUnavailable
from .scan_version import ScanVersionChanged, file_generation

logger = logging.getLogger(__name__)
TRANSIENT_ERRORS = (sqlite3.OperationalError, OperationalError, MaintenanceUnavailable)
SWEEP_SECONDS = 3600
SWEEP_RETRY_SECONDS = 60
WRITE_FAILED = "write_failed"


@dataclass(frozen=True)
class _MediaPin:
    path: Path
    version_id: str | None
    digest: str | None

    def generation(self) -> tuple[int, ...] | None:
        if self.digest is None:
            return None
        try:
            return file_generation(str(self.path))
        except ScanVersionChanged:
            return None


def _media_pin(db, snapshot) -> _MediaPin:
    media = Path(snapshot["media_path"])
    version_id = snapshot.get("media_version_id")
    source_review_id = snapshot.get("source_review_id")
    if version_id is None and source_review_id:
        version_id = review_version_id(db, source_review_id)
    version = get_version(db, version_id) if version_id else None
    return _MediaPin(media, version_id, version.content_sha256 if version is not None else None)


def _destination(db, row, snapshot):
    target = parse_export_id(row.target_export_id)
    destination = safe_destination(Path(snapshot["media_path"]), row.format, target)
    _check_destination(db, row, destination)
    return destination


def _retained_destination(db, row):
    """Return the retained path while its directory exists and resolves to itself.

    Withdrawal never re-resolves media, so a redirected library cannot steer recovery or
    deletion. Refusals raise before any file is read or changed; a directory that cannot
    be resolved raises OSError so the caller defers.
    """
    destination = Path(row.destination_path)
    if resolve_directory(destination.parent) != destination.parent or destination.is_symlink():
        raise ExportConflict("export_destination_changed", "Export destination changed")
    if destination.exists() and not destination.is_file():
        raise ExportConflict("export_destination_changed", "Export destination changed")
    _check_destination(db, row, destination)
    return destination


def _audit(db, row, job, *, reason):
    details = {
        "format": row.format.value,
        "target_kind": "personal",
        "policy_revision": job.policy_revision,
        "source_owner_id": job.policy_owner_id,
    }
    if reason is not None:
        details["reason"] = reason
    stage_event(
        db,
        AuditEvent(
            occurred_at=datetime.now(UTC),
            actor=None,
            credential=None,
            action=AuditAction.EXPORT_REGENERATE,
            outcome=AuditOutcome.SUCCEEDED if reason is None else AuditOutcome.FAILED,
            target_user=UserId(row.target_user_id),
            media_key=row.media_item_id,
            resource=f"export:{row.id}",
            details=details,
        ),
        actor_kind=ActorKind.SYSTEM,
    )


def _stage_result(db, row, snapshot, job, content, *, reason=None):
    """Stage a replacement, or a withdrawal for `reason` that keeps the owned digest."""
    owned = owned_digest(row)
    row.content_bytes = content
    row.publication_revision += 1
    row.exported_at = datetime.now(UTC)
    snapshot["publication_id"] = str(uuid4())
    row.policy_owner_id = job.policy_owner_id
    snapshot["policy_owner_id"] = job.policy_owner_id
    if reason is not None:
        if owned is not None:
            snapshot[OWNED_DIGEST] = owned
        finish(
            row.regeneration,
            RegenerationState.WITHDRAWN,
            RegenerationFailure.REGENERATION_FAILED,
        )
    else:
        snapshot.pop(OWNED_DIGEST, None)
        snapshot["policy_channel_evaluation"] = POLICY_CHANNEL_EVALUATION
        finish(row.regeneration, RegenerationState.SUCCEEDED)
        row.policy_revision = job.policy_revision
        snapshot["policy_revision"] = job.policy_revision
    save_snapshot(row, snapshot)
    _audit(db, row, job, reason=reason)


def _replace(db, row, snapshot, job, destination, content, *, reason=None):
    _stage_result(db, row, snapshot, job, content, reason=reason)
    with recoverable_sidecar(
        destination,
        content,
        journaled=True,
        committed_content=lambda: committed_export_content(db, job.export_id),
    ):
        db.commit()


def _defer(db, row):
    defer_withdrawal(row.regeneration)
    db.commit()
    return "failed"


def _withdraw(db, job, cancelled):
    """Remove only owned bytes at the retained path; keep their digest after withdrawal.

    ENOENT at the retained path records the withdrawal without a file operation. Any
    other filesystem error defers, because an outage cannot be told from a deletion.
    """
    db.rollback()
    with write_lock(db):
        admitted = current_job(db, job)
        if admitted is None or (cancelled is not None and cancelled.is_set()):
            db.rollback()
            return "stale"
        row, snapshot = admitted
        try:
            absent = retained_path_absent(Path(row.destination_path))
            destination = None if absent else _retained_destination(db, row)
        except TRANSIENT_ERRORS:
            raise
        except ExportConflict as exc:
            if exc.code != "export_file_collision":
                return _defer(db, row)
            _stage_result(db, row, snapshot, job, None, reason=WRITE_FAILED)
            db.commit()
            return "failed"
        except (ExportRecoveryRequired, OSError):
            return _defer(db, row)
        if destination is None:
            _stage_result(db, row, snapshot, job, None, reason="retained_path_absent")
            db.commit()
            return "failed"
        try:
            _replace(db, row, snapshot, job, destination, None, reason=WRITE_FAILED)
        except TRANSIENT_ERRORS:
            raise
        except (ExportRecoveryRequired, OSError):
            db.rollback()
            with write_lock(db):
                admitted = current_job(db, job)
                if admitted is None:
                    db.rollback()
                    return "stale"
                return _defer(db, admitted[0])
        return "failed"


def settle_regeneration(db, job: RegenerationJob, *, cancelled=None) -> str:
    """Settle one pinned claim; infrastructure failures retain its delayed retry."""
    try:
        admitted = current_job(db, job)
        if admitted is None or (cancelled is not None and cancelled.is_set()):
            db.rollback()
            return "stale"
        media = _media_pin(db, admitted[1])
        db.rollback()
        generation = media.generation()
        matches = generation is not None and file_digest_matches(media.path, media.digest)
        with publication_lock(db), write_lock(db):
            admitted = current_job(db, job)
            if admitted is None or (cancelled is not None and cancelled.is_set()):
                db.rollback()
                return "stale"
            row, snapshot = admitted
            if _media_pin(db, snapshot) != media or media.generation() != generation:
                db.rollback()
                return "retry"
            try:
                if media.version_id and not matches:
                    raise ExportConflict(
                        "export_media_changed", "The original review version is unavailable"
                    )
                destination = _destination(db, row, snapshot)
                row.policy_owner_id = job.policy_owner_id
                content = prepare_retained_export(db, row, snapshot)
            except TRANSIENT_ERRORS:
                raise
            except (ValueError, OSError):
                return _withdraw(db, job, cancelled)
            if cancelled is not None and cancelled.is_set():
                db.rollback()
                return "stale"
            if media.generation() != generation:
                db.rollback()
                return "retry"
            try:
                prepare_export_directory(
                    Path(snapshot["media_path"]),
                    row.format,
                    parse_export_id(row.target_export_id),
                    destination,
                )
                _replace(db, row, snapshot, job, destination, content)
            except TRANSIENT_ERRORS:
                raise
            except (OSError, ExportConflict, ExportRecoveryRequired):
                return _withdraw(db, job, cancelled)
            return "succeeded"
    except TRANSIENT_ERRORS:
        db.rollback()
        return "retry"


class ExportRegenerationScheduler:
    """Resume durable claims on startup and poll for committed policy changes."""

    def __init__(self, session_factory, *, data_dir=None, poll_seconds=2.0):
        if data_dir is None:
            from scanner.settings.config import settings

            data_dir = settings.data_dir
        self.session_factory = session_factory
        self.data_dir = Path(data_dir)
        self.poll_seconds = poll_seconds
        self.next_sweep = 0.0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="export-regeneration", daemon=True)

    def tick(self):
        if self.stop_event.is_set():
            return None
        from .maintenance import DatabaseMaintenance

        with DatabaseMaintenance(self.data_dir).worker_lease():
            if time.monotonic() >= self.next_sweep:
                self.next_sweep = time.monotonic() + SWEEP_RETRY_SECONDS
                try:
                    with self.session_factory() as db:
                        sweep_regenerations(db)
                except Exception:
                    logger.exception("Export regeneration discovery will retry")
                else:
                    self.next_sweep = time.monotonic() + SWEEP_SECONDS
            with self.session_factory() as db:
                job = claim_regeneration(db)
            if job is None:
                return None
            with self.session_factory() as db:
                return settle_regeneration(db, job, cancelled=self.stop_event)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Export regeneration remains pending for recovery")
            self.stop_event.wait(self.poll_seconds)

    def start(self):
        self.thread.start()

    def stop(self, timeout=10.0):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout)
        return not self.thread.is_alive()
