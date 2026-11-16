"""The words audit rows are written in: actions, categories, actor kinds, and rule shapes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum

from scanner.security.access.records import AuditEvent, AuditOutcome


class AuditAction(StrEnum):
    SETUP = "auth.setup"
    SIGN_IN = "auth.sign_in"
    ACCOUNT_CHANGE = "account.credentials.change"
    ACCOUNT_RECOVER = "account.credentials.recover"
    SESSIONS_REVOKE = "sessions.revoke"
    API_KEY_ISSUE = "api_key.issue"
    API_KEY_REVOKE = "api_key.revoke"
    API_KEY_REVEAL = "api_key.reveal"
    FILTER_POLICY_CHANGE = "filters.policy.change"
    EXPORT_PUBLISH = "export.publish"
    EXPORT_REGENERATE = "export.regenerate"
    EXPORT_DELETE = "export.delete"
    EXPORT_RETAIN = "export.retain"
    EXPORT_WRITE_COMMITTED = "export.write_committed"
    COMMUNITY_PUBLISH = "community.publication.publish"
    COMMUNITY_RETRY = "community.publication.retry"
    COMMUNITY_RETRACT = "community.publication.retract"
    COMMUNITY_IDENTITY_CHANGE = "community.identity.change"
    CONNECTION_CHANGE = "settings.connection.change"
    PATH_MAPPING_CHANGE = "settings.path_mapping.change"
    DEPLOYMENT_CHANGE = "settings.deployment.change"
    LOGGING_CHANGE = "settings.logging.change"
    RESTART = "system.restart"
    BACKUP_CREATE = "backup.create"
    BACKUP_DOWNLOAD = "backup.download"
    BACKUP_RESTORE = "backup.restore"
    BACKUP_DELETE = "backup.delete"
    BACKUP_SCHEDULE_CHANGE = "backup.schedule.change"
    ACCOUNT_CREATE = "account.create"
    ACCOUNT_UPDATE = "account.update"
    ACCOUNT_STATUS = "account.status"
    # No route records this or REGISTRATION_CHANGE since 0030; kept so stored events still read.
    ACCOUNT_PASSWORD_RESET = "account.password.reset"
    PERMISSIONS_CHANGE = "permissions.change"
    MEDIA_ACCESS_CHANGE = "media_access.change"
    REGISTRATION_CHANGE = "settings.registration.change"
    JELLYFIN_ADMISSION_CHANGE = "settings.jellyfin_admission.change"
    JELLYFIN_SYNC = "account.jellyfin.sync"
    JELLYFIN_ACCOUNT_STATE = "account.jellyfin.state"
    JELLYFIN_UNBLOCK = "account.jellyfin.unblock"
    ACCOUNT_LINK = "account.link"
    ACCOUNT_DELETE = "account.delete"
    OWNERSHIP_TRANSFER = "account.ownership.transfer"


class AuditCategory(StrEnum):
    SECURITY = "security"
    EXPORT = "export"
    COMMUNITY = "community"
    SETTINGS = "settings"


class ActorKind(StrEnum):
    """Who acted. `UNATTRIBUTED` is reserved for rows that predate attribution."""

    USER = "user"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"
    UNATTRIBUTED = "unattributed"


class Scope(Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    ABSENT = "absent"


class AuditEventRejected(ValueError):
    """An event that the registry does not allow. Producers treat it as a code defect."""


COUNT = int
FLAG = bool
DetailRule = frozenset[str] | type[int] | type[bool]


@dataclass(frozen=True)
class ActionSpec:
    """What one action may record. `actors` maps each allowed outcome to its actor kinds."""

    category: AuditCategory
    actors: Mapping[AuditOutcome, frozenset[ActorKind]]
    target_user: Scope
    media: Scope
    resource: Scope
    details: Mapping[str, DetailRule]
    required_details: frozenset[str] = frozenset()
    consistent: Callable[[AuditEvent], bool] | None = None
