"""General Settings controls for the macOS application shell."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool

from scanner.security.access.capability_ids import Capability as C
from scanner.settings.config import settings
from scanner.settings.macos_dock import (
    macos_launcher_available,
    read_show_in_dock,
    write_show_in_dock,
)

from .operations_access import operation

router = APIRouter()


class DockSettings(BaseModel):
    show_in_dock: StrictBool


@router.get("/api/settings/macos/dock", dependencies=[Depends(operation(C.DEPLOYMENT_VIEW))])
def get_dock_settings() -> DockSettings | None:
    """Expose the preference only for a packaged macOS app instance."""
    if not macos_launcher_available():
        return None
    try:
        return DockSettings(show_in_dock=read_show_in_dock(settings.data_dir))
    except (OSError, ValueError) as error:
        raise HTTPException(503, "Unable to load the Dock icon setting") from error


@router.put("/api/settings/macos/dock", dependencies=[Depends(operation(C.DEPLOYMENT_EDIT))])
def update_dock_settings(update: DockSettings) -> DockSettings:
    """Save a Dock preference for the running launcher to pick up."""
    if not macos_launcher_available():
        raise HTTPException(404, "Dock icon settings require the Editarr macOS app")
    try:
        write_show_in_dock(settings.data_dir, update.show_in_dock)
    except OSError as error:
        raise HTTPException(503, "Unable to save the Dock icon setting") from error
    return update
