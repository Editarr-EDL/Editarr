"""Capability rows for instance settings and operations, plus the settings-default table map."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .capability_ids import Capability as C
from .capability_spec import CapabilitySpec, spec
from .vocabulary import ADMINS, EVERYONE, NOBODY, OwnerPower, Target

_I = Target.INSTANCE
_CONNECTIONS = OwnerPower.CHANGE_CONNECTIONS_OR_DEPLOYMENT
_BACKUPS = OwnerPower.DOWNLOAD_BACKUPS

_VIEW_CONNECTIONS = "View connection addresses/status"
_MANAGE_CONNECTIONS = "Test/edit/disconnect Jellyfin"
_INVENTORY = "View installed models/global download status"
_DOWNLOAD = "Download models, including scan/credits-triggered acquisition"
_PROGRESS = "Own-job model-acquisition progress"
_MODEL_ADMIN = "Cancel model downloads/delete models"
_VIEW_MAPPINGS = "View/test path mappings"
_EDIT_MAPPINGS = "Create/edit/delete/reorder path mappings"
_DEPLOYMENT = "View/change deployment/network settings"
_LOGS = "View logs/change logging settings"
_RESTART = "Restart Editarr"
_VERSION = "View About/version"
_BACKUP = "Create backup/configure schedules/view backup job status"
_BACKUP_DOWNLOAD = "Download backup"
_BACKUP_DELETE = "Delete backup jobs/retained archives"
_AUDIT = "Read audit history"
_ARCHIVE = "View archived scans, reviews, and exports of removed sources"

OPERATION_CAPABILITIES: tuple[CapabilitySpec, ...] = (
    spec(C.CONNECTIONS_VIEW, _I, ADMINS, _VIEW_CONNECTIONS),
    # Testing submits connection settings, so it is grouped with changing them.
    spec(C.CONNECTIONS_TEST, _I, NOBODY, _MANAGE_CONNECTIONS, owner_power=_CONNECTIONS),
    spec(C.CONNECTIONS_EDIT, _I, NOBODY, _MANAGE_CONNECTIONS, owner_power=_CONNECTIONS),
    spec(C.CONNECTIONS_DISCONNECT, _I, NOBODY, _MANAGE_CONNECTIONS, owner_power=_CONNECTIONS),
    spec(C.MODELS_INVENTORY_VIEW, _I, ADMINS, _INVENTORY),
    spec(C.MODELS_DOWNLOAD, _I, EVERYONE, _DOWNLOAD),
    spec(C.MODELS_PROGRESS_VIEW_OWN, Target.OWN_RESOURCE, EVERYONE, _PROGRESS),
    spec(C.MODELS_DOWNLOAD_CANCEL, _I, ADMINS, _MODEL_ADMIN),
    spec(C.MODELS_DELETE, _I, ADMINS, _MODEL_ADMIN),
    spec(C.PATH_MAPPINGS_VIEW, _I, ADMINS, _VIEW_MAPPINGS),
    spec(C.PATH_MAPPINGS_TEST, _I, ADMINS, _VIEW_MAPPINGS),
    spec(C.PATH_MAPPINGS_CREATE, _I, NOBODY, _EDIT_MAPPINGS),
    spec(C.PATH_MAPPINGS_UPDATE, _I, NOBODY, _EDIT_MAPPINGS),
    spec(C.PATH_MAPPINGS_DELETE, _I, NOBODY, _EDIT_MAPPINGS),
    spec(C.PATH_MAPPINGS_REORDER, _I, NOBODY, _EDIT_MAPPINGS),
    spec(C.DEPLOYMENT_VIEW, _I, NOBODY, _DEPLOYMENT),
    spec(C.DEPLOYMENT_EDIT, _I, NOBODY, _DEPLOYMENT, owner_power=_CONNECTIONS),
    spec(C.LOGS_VIEW, _I, ADMINS, _LOGS),
    spec(C.LOGS_SETTINGS_EDIT, _I, ADMINS, _LOGS),
    spec(C.SYSTEM_RESTART, _I, ADMINS, _RESTART),
    spec(C.SYSTEM_VERSION_VIEW, _I, EVERYONE, _VERSION),
    spec(C.BACKUPS_CREATE, _I, ADMINS, _BACKUP),
    spec(C.BACKUPS_SCHEDULE_EDIT, _I, ADMINS, _BACKUP),
    spec(C.BACKUPS_STATUS_VIEW, _I, ADMINS, _BACKUP),
    spec(C.BACKUPS_DOWNLOAD, _I, NOBODY, _BACKUP_DOWNLOAD, owner_power=_BACKUPS),
    spec(C.BACKUPS_DELETE, _I, NOBODY, _BACKUP_DELETE),
    spec(C.AUDIT_READ, Target.SCOPED_RECORDS, ADMINS, _AUDIT),
    # The archive lists every archived review and path without media or account
    # checks, so only the admin role and the owner may ever read it.
    spec(C.ARCHIVE_VIEW, _I, ADMINS, _ARCHIVE, role_fixed=True),
)

SETTINGS_DECISION_ROWS: Mapping[str, tuple[C, ...]] = MappingProxyType(
    {
        "View connection addresses/status": (C.CONNECTIONS_VIEW,),
        "Test/edit/disconnect Jellyfin": (
            C.CONNECTIONS_TEST,
            C.CONNECTIONS_EDIT,
            C.CONNECTIONS_DISCONNECT,
        ),
        "View installed models and download status": (C.MODELS_INVENTORY_VIEW,),
        "Download models, including scan/credits-triggered acquisition": (C.MODELS_DOWNLOAD,),
        "Cancel model downloads": (C.MODELS_DOWNLOAD_CANCEL,),
        "Delete models": (C.MODELS_DELETE,),
        "View/test path mappings": (C.PATH_MAPPINGS_VIEW, C.PATH_MAPPINGS_TEST),
        "Create/edit/delete/reorder path mappings": (
            C.PATH_MAPPINGS_CREATE,
            C.PATH_MAPPINGS_UPDATE,
            C.PATH_MAPPINGS_DELETE,
            C.PATH_MAPPINGS_REORDER,
        ),
        "View deployment/network configuration": (C.DEPLOYMENT_VIEW,),
        "Change deployment/network configuration": (C.DEPLOYMENT_EDIT,),
        "View diagnostic logs": (C.LOGS_VIEW,),
        "Change logging settings": (C.LOGS_SETTINGS_EDIT,),
        "Restart Editarr": (C.SYSTEM_RESTART,),
        "View About/version information": (C.SYSTEM_VERSION_VIEW,),
        "Edit instance filter selection rules and severity defaults": (
            C.FILTERS_INSTANCE_DEFAULTS_EDIT,
        ),
        "Change default filter mode (live inheritance or independent copy)": (
            C.FILTERS_INSTANCE_MODE_EDIT,
        ),
        "Choose an individual user's filter mode": (C.FILTERS_MODE_ASSIGN_OTHER,),
        "Enforce non-overridable filter defaults": (C.FILTERS_POLICY_ENFORCE,),
        "Create a full backup": (C.BACKUPS_CREATE,),
        "Download a full backup": (C.BACKUPS_DOWNLOAD,),
        "Configure scheduled backups": (C.BACKUPS_SCHEDULE_EDIT,),
        "View backup job/status details": (C.BACKUPS_STATUS_VIEW,),
        "Delete backup jobs or retained archives": (C.BACKUPS_DELETE,),
    }
)
"""Each row of the decision log's settings-default table and the capabilities it sets."""
