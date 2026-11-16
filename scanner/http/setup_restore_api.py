"""Setup-only, flow-bound restore of a backup into an empty installation."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import closing, suppress
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from scanner.backup.activation import activate_restore
from scanner.backup.archive import MAX_TOTAL, BackupError
from scanner.backup.restore import preview_restore
from scanner.backup.restore_target import RestoreRefused, require_empty_installation
from scanner.http import background, backup_api
from scanner.persistence.migrations.preflight import database_location
from scanner.runtime.application_paths import unique_cookie_value
from scanner.security.api_keys import seal_database_api_keys

router = APIRouter(prefix="/api/setup-restore", tags=["setup-restore"])
_flows: dict[str, tuple[str, float]] = {}
_guard = Lock()
_LIFETIME = 30 * 60
_MAX_FLOWS = 16
_COOKIE = "editarr_setup_restore"
_UPLOAD_LIMIT = MAX_TOTAL + 2 * 1024 * 1024


class RestoreConfirmation(BaseModel):
    confirmed: Literal[True]


def require_unconfigured_database(database: Path) -> None:
    """Fail closed when setup was completed, including during the exclusive switch."""
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        if connection.execute("SELECT 1 FROM users WHERE role = 'owner' LIMIT 1").fetchone():
            raise HTTPException(409, "Administrator setup is already complete")


def _require_empty(database: Path, data_dir: Path) -> None:
    try:
        require_empty_installation(data_dir, database)
    except RestoreRefused as refusal:
        raise HTTPException(409, {"code": refusal.reason, "message": str(refusal)}) from None


def _live(owner: str) -> tuple[str, float]:
    """Return the owner's unexpired flow; call with `_guard` held."""
    flow = _flows.get(owner)
    if flow is None or time.monotonic() > flow[1]:
        _flows.pop(owner, None)
        raise HTTPException(403, "Setup restore flow expired")
    return flow


def _discard_orphaned_jobs() -> None:
    """Cancel unfinished jobs whose flow was replaced or expired; activation is never cancelled."""
    with backup_api._guard:
        unfinished = [
            (job.job_id, job.owner)
            for job in backup_api._jobs.values()
            if job.owner
            and not job.cancelled
            and job.state in ("waiting", "working", "uploaded", "ready")
        ]
    with _guard:
        now = time.monotonic()
        orphaned = [
            (job_id, owner) for job_id, owner in unfinished if _flows.get(owner, ("", 0.0))[1] < now
        ]
    for job_id, owner in orphaned:
        with suppress(HTTPException):
            backup_api.discard_backup(job_id, owner=owner)


def _owner(request: Request) -> str:
    database, _ = database_location()
    require_unconfigured_database(database)
    token = unique_cookie_value(request, _COOKIE) or ""
    owner = hashlib.sha256(token.encode()).hexdigest() if token else ""
    with _guard:
        flow = _live(owner)
    if request.method not in {"GET", "HEAD"} and not hmac.compare_digest(
        request.headers.get("x-setup-restore-token", ""), flow[0]
    ):
        raise HTTPException(403, "Setup restore request verification failed")
    return owner


@router.post("/flow")
def begin_flow(request: Request, response: Response):
    """Start a restore flow, refusing an installation that is not empty.

    The new cookie replaces the browser's previous flow cookie, so that flow and its
    unfinished jobs are ended rather than left holding a slot until they expire.
    """
    database, data_dir = database_location()
    require_unconfigured_database(database)
    _require_empty(database, data_dir)
    previous = unique_cookie_value(request, _COOKIE)
    replaced = hashlib.sha256(previous.encode()).hexdigest() if previous else None
    token = secrets.token_urlsafe(32)
    owner = hashlib.sha256(token.encode()).hexdigest()
    csrf = secrets.token_urlsafe(32)
    with _guard:
        _flows.pop(replaced, None)
        for existing, (_, expires) in list(_flows.items()):
            if time.monotonic() > expires:
                _flows.pop(existing)
        if len(_flows) >= _MAX_FLOWS:
            raise HTTPException(429, "Too many setup restore flows")
        _flows[owner] = (csrf, time.monotonic() + _LIFETIME)
    _discard_orphaned_jobs()
    snapshot = request.app.state.deployment_snapshot
    response.set_cookie(
        _COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=snapshot.public_origin.scheme == "https",
        path=snapshot.prefix,
        max_age=_LIFETIME,
    )
    response.headers["Cache-Control"] = "no-store"
    return {"token": csrf}


@router.post("/jobs")
def start_job(request: Request):
    owner = _owner(request)
    database, data_dir = database_location()
    _require_empty(database, data_dir)
    _discard_orphaned_jobs()
    # Admit under the flow lock so a flow replaced since _owner() cannot gain a job
    # that its replacement's cleanup already missed.
    with _guard:
        _live(owner)
        job = backup_api.new_job("restore", owner=owner)
    return backup_api._public(job)


@router.get("/jobs/{job_id}")
def status(job_id: str, request: Request):
    return backup_api._public(backup_api._get(job_id, owner=_owner(request)))


@router.put("/jobs/{job_id}/archive")
async def upload(job_id: str, request: Request):
    """Stream one bounded archive into the job's private staging."""
    job = backup_api._get(job_id, owner=_owner(request))
    with backup_api._guard:
        if job.action != "restore" or job.state != "waiting":
            raise HTTPException(409, "Restore job is not awaiting an archive")
        job.state = "working"
    size = 0
    try:
        with job.archive.open("xb") as writer:
            os.chmod(job.archive, 0o600)
            async for chunk in request.stream():
                if job.cancelled:
                    raise BackupError("Backup upload cancelled")
                size += len(chunk)
                if size > _UPLOAD_LIMIT:
                    raise BackupError("Backup archive exceeds the upload limit")
                writer.write(chunk)
        if size == 0:
            raise BackupError("Backup archive is empty")
        with backup_api._guard:
            if job.cancelled:
                raise BackupError("Backup upload cancelled")
            job.state = "uploaded"
        return backup_api._public(job)
    except Exception:
        with backup_api._guard:
            job.archive.unlink(missing_ok=True)
            backup_api._cleanup_directory(job)
            job.state = "cancelled" if job.cancelled else "failed"
            job.error = (
                None if job.cancelled else "Backup upload failed or exceeded the archive limit"
            )
        raise HTTPException(400, job.error) from None


@router.post("/jobs/{job_id}/preflight")
def preflight(job_id: str, request: Request, payload: dict[str, str | None]):
    """Validate the archive and stage it; the job becomes ready with a redacted plan."""
    job = backup_api._get(job_id, owner=_owner(request))
    if job.action != "restore" or job.state != "uploaded":
        raise HTTPException(409, "Restore archive is not ready")
    passphrase = payload.get("passphrase")
    if passphrase is not None and (not isinstance(passphrase, str) or len(passphrase) > 4096):
        raise HTTPException(400, "Invalid passphrase")

    def validate():
        with preview_restore(
            job.archive, job.data_dir, target_database=job.database, passphrase=job.passphrase
        ) as preview:
            with backup_api._guard:
                job.plan = preview.plan

    backup_api._submit(
        job,
        validate,
        "ready",
        expected_state="uploaded",
        authority=background.SystemWork.SETUP_RESTORE,
        set_passphrase=True,
        passphrase=passphrase,
    )
    return backup_api._public(job)


@router.post("/jobs/{job_id}/activate")
def activate(job_id: str, request: Request, confirmation: RestoreConfirmation):
    """Restore the previewed archive. Activation checks the target is still empty."""
    job = backup_api._get(job_id, owner=_owner(request))
    if job.action != "restore" or job.state != "ready" or job.plan is None:
        raise HTTPException(409, "Restore preview is not ready")

    def restore():
        with preview_restore(
            job.archive, job.data_dir, target_database=job.database, passphrase=job.passphrase
        ) as preview:
            if preview.plan != job.plan:
                raise BackupError("Backup preview changed since confirmation")
            report = activate_restore(preview, job.database, job.data_dir, confirmed=True)
        # Startup seals too; this keeps a restored older backup from serving plaintext until then.
        seal_database_api_keys(job.database, job.data_dir)
        job.report = {"move_disagreements": len(report["move_disagreements"])}
        job.plan = None
        job.archive.unlink(missing_ok=True)

    backup_api._submit(
        job,
        restore,
        "activated",
        authority=background.SystemWork.SETUP_RESTORE,
        expected_state="ready",
    )
    return backup_api._public(job)


@router.delete("/jobs/{job_id}")
def cancel(job_id: str, request: Request):
    return backup_api.discard_backup(job_id, owner=_owner(request))
