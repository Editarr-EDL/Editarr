"""Configuration and redacted status for unattended backups."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import JSONResponse

from scanner.backup.archive import BackupError
from scanner.backup.schedule import ScheduleBusy, disable_schedule, save_schedule, schedule_status
from scanner.backup.schedule_store import ScheduleConfig
from scanner.persistence.migrations.preflight import database_location
from scanner.runtime.maintenance import MaintenanceUnavailable
from scanner.runtime.production_frontend import served_roots
from scanner.security.access.capability_ids import Capability as C
from scanner.security.audit.actions import AuditAction

from .operations_access import Operator, operation

router = APIRouter(prefix="/api/backups/schedule", tags=["backups"])
_CHANGE = AuditAction.BACKUP_SCHEDULE_CHANGE
_EDIT = operation(C.BACKUPS_SCHEDULE_EDIT, _CHANGE)


class ConfigureSchedule(ScheduleConfig):
    confirmed: Literal[True]


def _respond(caller: Operator, write=None, *, change: str | None = None):
    _database, data_dir = database_location()
    try:
        if write is not None:
            write(data_dir)
        status = schedule_status(data_dir)
    except (ScheduleBusy, MaintenanceUnavailable) as error:
        raise HTTPException(409, "Backup maintenance is busy; retry after it finishes") from error
    except (BackupError, OSError, ValueError) as error:
        raise HTTPException(
            400, "Backup schedule unavailable; check destination and credential permissions"
        ) from error
    if change is not None:
        caller.record(_CHANGE, {"change": change})
    if status["config"] is not None and not caller.sees_paths():
        status["config"] = {**status["config"], "destination": None, "passphrase_file": None}
    return JSONResponse(status, headers={"Cache-Control": "no-store"})


@router.get("")
def get_schedule(caller: Operator = Depends(operation(C.BACKUPS_STATUS_VIEW))):
    return _respond(caller)


@router.put("")
def configure_schedule(
    request: ConfigureSchedule, http: Request, caller: Operator = Depends(_EDIT)
):
    config = ScheduleConfig.model_validate(request.model_dump(exclude={"confirmed"}))
    roots = served_roots(http.app)
    return _respond(
        caller,
        lambda data_dir: save_schedule(data_dir, config, served_roots=roots),
        change="configured",
    )


@router.delete("")
def stop_schedule(caller: Operator = Depends(_EDIT)):
    return _respond(caller, disable_schedule, change="disabled")
