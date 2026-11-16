"""Explicitly enabled interval backups with durable attempts and owned-file retention."""

from __future__ import annotations

import os
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from scanner.runtime.maintenance import DatabaseMaintenance, _lock, _open_lock, _unlock

from .archive import BackupError, _digest, export_backup
from .schedule_store import (
    ScheduleConfig,
    ScheduledArchive,
    ScheduleState,
    destination_identity,
    read_passphrase,
    read_state,
    write_state,
)
from .snapshots import _sync_directory


class ScheduleBusy(BackupError):
    """A scheduled export or settings update already owns the schedule."""


@contextmanager
def schedule_lock(data_dir: Path):
    maintenance = DatabaseMaintenance(data_dir)
    descriptor = _open_lock(maintenance.directory / "backup-schedule.lock")
    acquired = False
    try:
        acquired = _lock(descriptor, exclusive=True, blocking=False)
        if not acquired:
            raise ScheduleBusy("A scheduled backup is running; retry when it finishes")
        yield
    finally:
        if acquired:
            _unlock(descriptor)
        os.close(descriptor)


def save_schedule(
    data_dir: Path,
    config: ScheduleConfig,
    *,
    now: float | None = None,
    served_roots: tuple[Path, ...] = (),
):
    """Require explicit configuration and serialize changes against active exports."""
    now = time.time() if now is None else now
    with schedule_lock(data_dir), DatabaseMaintenance(data_dir).worker_lease():
        state = read_state(data_dir)
        if config.enabled:
            identity = destination_identity(config, data_dir, served_roots)
            read_passphrase(config, data_dir)
        else:
            identity = (state.destination_device, state.destination_inode)
        same_destination = (
            state.config is not None
            and state.config.destination == config.destination
            and identity == (state.destination_device, state.destination_inode)
        )
        if not same_destination:
            state = ScheduleState(owner=uuid.uuid4().hex)
        state.owner = state.owner or uuid.uuid4().hex
        unchanged_timing = (
            state.config is not None
            and state.config.enabled
            and state.config.interval_hours == config.interval_hours
        )
        state.next_run = (
            (
                state.next_run
                if unchanged_timing and state.next_run is not None
                else now + config.interval_hours * 3600
            )
            if config.enabled
            else None
        )
        state.config = config
        state.notice = None
        state.destination_device, state.destination_inode = identity
        if state.outcome == "running":
            state.outcome = "interrupted"
            state.error = "previous_run_interrupted"
        write_state(data_dir, state)
        return state


def disable_schedule(data_dir: Path):
    with schedule_lock(data_dir), DatabaseMaintenance(data_dir).worker_lease():
        state = read_state(data_dir)
        if state.config:
            state.config = state.config.model_copy(update={"enabled": False})
            state.next_run = None
            write_state(data_dir, state)
        return state


def _output_directory(data_dir: Path, state: ScheduleState) -> Path:
    config = state.config
    if config is None or not state.owner:
        raise BackupError("Invalid scheduled backup ownership")
    if destination_identity(config, data_dir) != (
        state.destination_device,
        state.destination_inode,
    ):
        raise BackupError("Backup destination changed; configure it again")
    directory = Path(config.destination) / f"editarr-scheduled-{state.owner}"
    if state.output_inode is None:
        directory.mkdir(mode=0o700)
        info = directory.stat()
        state.output_device, state.output_inode = info.st_dev, info.st_ino
        _sync_directory(directory.parent)
        write_state(data_dir, state)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != (state.output_device, state.output_inode)
        or (
            os.name == "posix"
            and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077)
        )
    ):
        raise BackupError("Scheduled backup directory is unsafe")
    return directory


def _prune(data_dir: Path, state: ScheduleState, directory: Path) -> None:
    if state.config is None:
        return
    while len(state.archives) > state.config.keep_count:
        oldest = state.archives[0]
        path = directory / oldest.name
        try:
            before = path.lstat()
        except FileNotFoundError:
            state.archives.pop(0)
            write_state(data_dir, state)
            continue
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != oldest.size
            or _digest(path) != oldest.sha256
            or _file_identity(path.lstat()) != _file_identity(before)
        ):
            raise BackupError("An owned backup changed; retention requires review")
        current = directory.lstat()
        if (current.st_dev, current.st_ino) != (state.output_device, state.output_inode):
            raise BackupError("Scheduled backup directory changed")
        path.unlink()
        _sync_directory(directory)
        state.archives.pop(0)
        write_state(data_dir, state)


def _file_identity(info: os.stat_result) -> tuple:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def run_due(database: Path, data_dir: Path, *, clock=time.time) -> bool:
    """Run at most one due attempt; persist its next interval before doing any work."""
    initial = read_state(data_dir)
    if initial.config is None or not initial.config.enabled:
        return False
    try:
        with schedule_lock(data_dir):
            state = read_state(data_dir)
            config = state.config
            if config is None or not config.enabled:
                return False
            if any(
                (data_dir / name).exists()
                for name in (
                    "restore-activation.json",
                    "restore-reconciliation.json",
                )
            ):
                return False
            now = float(clock())
            if state.outcome == "running":
                state.outcome = "interrupted"
                state.error = "previous_run_interrupted"
                write_state(data_dir, state)
            if state.next_run is None or now < state.next_run:
                return False
            state.last_attempt = now
            state.next_run = now + config.interval_hours * 3600
            state.outcome, state.error = "running", None
            write_state(data_dir, state)
            error_code = "destination_unavailable"
            try:
                directory = _output_directory(data_dir, state)
                error_code = "credential_unavailable"
                passphrase = read_passphrase(config, data_dir)
                error_code = "retention_requires_review"
                if len(state.archives) >= 1001:
                    raise BackupError("Too many retained backups require review")
                extension = "age" if config.protection == "age-scrypt" else "backup"
                destination = directory / f"{uuid.uuid4().hex}.{extension}"
                error_code = "export_failed"
                try:
                    export_backup(
                        database,
                        data_dir,
                        destination,
                        passphrase=passphrase,
                        replace_existing=False,
                        require_reconciled=True,
                    )
                finally:
                    passphrase = None
                completed = float(clock())
                state.archives.append(
                    ScheduledArchive(
                        name=destination.name,
                        sha256=_digest(destination),
                        size=destination.stat().st_size,
                        created_at=completed,
                    )
                )
                state.last_success = completed
                state.outcome = "succeeded"
                state.next_run = completed + config.interval_hours * 3600
                write_state(data_dir, state)
                error_code = "retention_requires_review"
                _prune(data_dir, state, directory)
            except Exception:
                state.error = error_code
                if state.outcome == "running":
                    state.outcome = "failed"
                state.next_run = float(clock()) + config.interval_hours * 3600
                write_state(data_dir, state)
            return True
    except ScheduleBusy:
        return False


def schedule_status(data_dir: Path) -> dict:
    state = read_state(data_dir)
    return {
        "config": state.config.model_dump() if state.config else None,
        "next_run": state.next_run,
        "last_attempt": state.last_attempt,
        "last_success": state.last_success,
        "outcome": state.outcome,
        "error": state.error,
        "retained_count": len(state.archives),
        "notice": state.notice,
        "restore_hold": any(
            (data_dir / name).exists()
            for name in (
                "restore-activation.json",
                "restore-reconciliation.json",
            )
        ),
    }
