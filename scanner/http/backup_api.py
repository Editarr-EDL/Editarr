"""Protected backup export jobs and the job engine setup restore shares.

An export runs for the admin who started it, who must still hold
`backups.create` when it starts and when its archive becomes ready.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from starlette.responses import FileResponse

from scanner.backup.activation import HOLD, JOURNAL
from scanner.backup.archive import export_backup
from scanner.backup.restore_target import RestoreRefused
from scanner.persistence.migrations.preflight import database_location
from scanner.runtime.runtime_paths import ensure_user_private_directory
from scanner.security.access.capability_ids import Capability as C
from scanner.security.audit.actions import AuditAction
from scanner.security.scan_jobs.authority import JobRefusedError
from scanner.security.scan_jobs.operation_gate import OperationGate

from . import background
from .operations_access import Operator, operation

router = APIRouter(prefix="/api/backups/jobs", tags=["backups"])
_guard = Lock()
_jobs: dict[str, BackupJob] = {}
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="backup-ui")
_MAX_JOBS = 4
_LIFETIME = 30 * 60
INITIATOR_REFUSED = "Stopped: the account that started this backup may no longer create backups."


@dataclass
class BackupJob:
    job_id: str
    action: str
    directory: Path
    database: Path
    data_dir: Path
    archive: Path
    owner: str | None = field(default=None, repr=False)
    passphrase: str | None = field(default=None, repr=False)
    state: str = "waiting"
    error: str | None = None
    reason: str | None = None
    plan: dict | None = None
    inventory: dict | None = None
    report: dict | None = None
    cancelled: bool = False
    activating: bool = False
    created: float = field(default_factory=time.monotonic)


class StartBackup(BaseModel):
    action: Literal["export"] = "export"
    kind: Literal["full"] = "full"
    encrypt: bool = False
    passphrase: str | None = Field(default=None, max_length=4096, repr=False)


_STATUS = [Depends(operation(C.BACKUPS_STATUS_VIEW))]


def _get(job_id: str, *, owner: str | None = None) -> BackupJob:
    with _guard:
        job = _jobs.get(job_id)
    if job is None or job.owner != owner:
        raise HTTPException(404, "Backup job not found")
    if job.state not in ("working", "activating") and time.monotonic() - job.created > _LIFETIME:
        with _guard:
            if job.state not in ("working", "activating"):
                _jobs.pop(job_id, None)
                _cleanup_directory(job)
                raise HTTPException(404, "Backup job expired")
    return job


def _public(job: BackupJob) -> dict:
    return {
        "id": job.job_id,
        "action": job.action,
        "state": job.state,
        "kind": "full",
        "error": job.error,
        "reason": job.reason,
        "plan": job.plan,
        "inventory": job.inventory,
        "report": job.report,
    }


def _cleanup_directory(job: BackupJob) -> None:
    shutil.rmtree(job.directory, ignore_errors=True)


def _counts_toward_admission(job: BackupJob) -> bool:
    if job.state in ("working", "activating"):
        return True
    return not job.cancelled and job.state != "failed"


def _complete(job: BackupJob, operation, state: str) -> None:
    try:
        result = operation()
        with _guard:
            if job.cancelled:
                job.plan = None
                job.archive.unlink(missing_ok=True)
                _cleanup_directory(job)
                job.state = "cancelled"
            else:
                if job.action == "export":
                    job.inventory = {
                        "protection": result["protection"],
                        "included_secret_types": result["included_secret_types"],
                        "omitted_environment": result["omitted_environment"],
                        "identity_included": result["identity_included"],
                    }
                job.state = state
    except Exception as error:
        with _guard:
            committed = (
                job.activating
                and (job.data_dir / HOLD).exists()
                and not (job.data_dir / JOURNAL).exists()
            )
            if job.cancelled:
                job.error = None
            elif isinstance(error, JobRefusedError):
                job.error = INITIATOR_REFUSED
            elif isinstance(error, RestoreRefused):
                job.error, job.reason = str(error), error.reason
            elif committed:
                job.error = "Restore committed; review reconciliation before resuming."
            else:
                job.error = (
                    f"Backup operation failed ({type(error).__name__}). Check archive and disk."
                )
            job.plan = None
            job.archive.unlink(missing_ok=True)
            if job.cancelled or not committed:
                _cleanup_directory(job)
            job.state = "cancelled" if job.cancelled else ("activated" if committed else "failed")
    finally:
        with _guard:
            if job.state != "activating":
                job.activating = False
            if job.action == "export" or job.state != "ready":
                job.passphrase = None
            if job.state in ("cancelled", "failed"):
                _cleanup_directory(job)


def _submit(
    job: BackupJob,
    operation,
    state: str,
    *,
    authority: background.Authority,
    expected_state: str,
    set_passphrase: bool = False,
    passphrase: str | None = None,
) -> None:
    with _guard:
        if job.state != expected_state or job.cancelled:
            raise HTTPException(409, "Backup job is not available")
        if set_passphrase:
            job.passphrase = passphrase
        job.state = "activating" if state == "activated" else "working"
        if state == "activated":
            job.activating = True
    background.submit(_executor, authority, lambda: _complete(job, operation, state))


@router.post("")
def start_backup(
    request: StartBackup,
    caller: Operator = Depends(operation(C.BACKUPS_CREATE, AuditAction.BACKUP_CREATE)),
):
    """Start a full export."""

    if request.encrypt != (request.passphrase is not None):
        raise HTTPException(400, "Encrypted export requires a passphrase")
    job = new_job("export", passphrase=request.passphrase)
    gate = OperationGate.for_request(caller.access, caller.db, C.BACKUPS_CREATE)

    def export():
        gate.require()
        result = export_backup(job.database, job.data_dir, job.archive, passphrase=job.passphrase)
        gate.require()
        return result

    _submit(job, export, "ready", authority=gate, expected_state="waiting")
    caller.record(
        AuditAction.BACKUP_CREATE,
        {"kind": "full", "encrypted": request.encrypt},
        resource=job.job_id,
    )
    return _public(job)


def new_job(action: str, *, owner: str | None = None, passphrase: str | None = None) -> BackupJob:
    """Admit one job with private staging; `owner` binds a setup restore flow."""
    database, data_dir = database_location()
    parent = data_dir / "backup-ui-jobs"
    ensure_user_private_directory(parent)
    with _guard:
        for job_id, old in list(_jobs.items()):
            expired = time.monotonic() - old.created > _LIFETIME
            if old.state not in ("working", "activating") and expired:
                _jobs.pop(job_id)
                _cleanup_directory(old)
        if sum(_counts_toward_admission(old) for old in _jobs.values()) >= _MAX_JOBS:
            raise HTTPException(429, "Too many backup jobs; cancel a finished job first")
        directory = Path(tempfile.mkdtemp(prefix="job-", dir=parent))
        job_id = uuid.uuid4().hex
        job = BackupJob(
            job_id,
            action,
            directory,
            database,
            data_dir,
            directory / "archive",
            owner=owner,
            passphrase=passphrase,
        )
        _jobs[job_id] = job
    return job


@router.get("/{job_id}", dependencies=_STATUS)
def backup_status(job_id: str):
    return _public(_get(job_id))


@router.get("/{job_id}/download")
def download_backup(
    job_id: str,
    caller: Operator = Depends(operation(C.BACKUPS_DOWNLOAD, AuditAction.BACKUP_DOWNLOAD)),
):
    job = _get(job_id)
    if job.action != "export" or job.state != "ready" or not job.archive.is_file():
        raise HTTPException(409, "Backup is not ready for download")
    with job.archive.open("rb") as stream:
        encrypted = stream.read(22) == b"age-encryption.org/v1\n"
    suffix = "age" if encrypted else "backup"
    caller.record(AuditAction.BACKUP_DOWNLOAD, {"kind": "full"}, resource=job_id)
    return FileResponse(
        job.archive,
        media_type="application/octet-stream",
        filename=f"editarr-full.{suffix}",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.head("/{job_id}/download", dependencies=[Depends(operation(C.BACKUPS_DOWNLOAD))])
def check_download(job_id: str):
    job = _get(job_id)
    if job.action != "export" or job.state != "ready" or not job.archive.is_file():
        raise HTTPException(409, "Backup download is unavailable")
    return Response(headers={"Cache-Control": "no-store"})


@router.delete("/{job_id}")
def cancel_backup(
    job_id: str,
    caller: Operator = Depends(operation(C.BACKUPS_DELETE, AuditAction.BACKUP_DELETE)),
):
    job = discard_backup(job_id)
    caller.record(AuditAction.BACKUP_DELETE, {}, resource=job_id)
    return job


def discard_backup(job_id: str, *, owner: str | None = None):
    job = _get(job_id, owner=owner)
    with _guard:
        if job.activating:
            raise HTTPException(409, "Restore activation cannot be cancelled")
        job.cancelled = True
        if job.state == "working":
            return _public(job)
        job.state = "cancelled"
        job.plan = None
        job.passphrase = None
        _cleanup_directory(job)
    return _public(job)
