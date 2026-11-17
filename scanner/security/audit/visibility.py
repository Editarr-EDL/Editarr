"""Live capabilities governing audit actions, including target and detail variants."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from scanner.security.access.capability_ids import Capability
from scanner.security.access.catalog import CAPABILITIES
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.vocabulary import Role

from .actions import AuditAction

Required = Capability | frozenset[Capability]


@dataclass(frozen=True)
class ByAccount:
    own: Required | None
    other: Required | None


@dataclass(frozen=True)
class ByDetail:
    key: str
    choices: Mapping[str | bool, Required | ByAccount | None]


_PERSONAL_PUBLISH = ByAccount(
    Capability.EXPORTS_PERSONAL_PUBLISH_OWN, Capability.REVIEWS_EXPORT_OTHER
)
_EXPORT_PUBLISH = ByDetail(
    "target_kind",
    {"personal": _PERSONAL_PUBLISH, "item_default": Capability.EXPORTS_DEFAULT_PUBLISH},
)
_IDENTITY_ENROLL = ByAccount(
    Capability.COMMUNITY_IDENTITY_ENROLL_OWN, Capability.COMMUNITY_IDENTITY_ENROLL_OTHER
)
_IDENTITY_PROFILE = ByAccount(
    Capability.COMMUNITY_IDENTITY_PROFILE_OWN, Capability.COMMUNITY_IDENTITY_PROFILE_OTHER
)
_IDENTITY_KEYS = ByAccount(
    Capability.COMMUNITY_IDENTITY_KEYS_OWN, Capability.COMMUNITY_IDENTITY_KEYS_OTHER
)

ACTION_CAPABILITIES: Mapping[AuditAction, Required | ByAccount | ByDetail | None] = (
    MappingProxyType(
        {
            AuditAction.SETUP: None,
            AuditAction.SIGN_IN: Capability.ACCOUNTS_LIST,
            AuditAction.ACCOUNT_CHANGE: ByDetail(
                "changed",
                {
                    "username": ByAccount(Capability.PROFILE_EDIT_OWN, None),
                    "password": ByAccount(Capability.PROFILE_EDIT_OWN, None),
                    "password_and_username": ByAccount(Capability.PROFILE_EDIT_OWN, None),
                },
            ),
            AuditAction.ACCOUNT_RECOVER: None,
            AuditAction.SESSIONS_REVOKE: ByAccount(None, Capability.ACCOUNTS_SESSIONS_REVOKE_OTHER),
            AuditAction.API_KEY_ISSUE: ByDetail(
                "replaced",
                {
                    False: ByAccount(
                        Capability.API_KEYS_CREATE_OWN, Capability.API_KEYS_ISSUE_OTHER
                    ),
                    True: ByAccount(
                        Capability.API_KEYS_ROTATE_OWN, Capability.API_KEYS_ISSUE_OTHER
                    ),
                },
            ),
            AuditAction.API_KEY_REVOKE: ByAccount(
                Capability.API_KEYS_REVOKE_OWN, Capability.API_KEYS_REVOKE_OTHER
            ),
            AuditAction.API_KEY_REVEAL: ByAccount(
                Capability.API_KEYS_REVEAL_OWN, Capability.API_KEYS_REVEAL_OTHER
            ),
            AuditAction.FILTER_POLICY_CHANGE: ByDetail(
                "cause",
                {
                    "enforcement": Capability.FILTERS_POLICY_ENFORCE,
                    "instance_defaults": frozenset(
                        {
                            Capability.FILTERS_INSTANCE_DEFAULTS_EDIT,
                            Capability.FILTERS_POLICY_ENFORCE,
                        }
                    ),
                    "ownership_transfer": Capability.OWNERSHIP_TRANSFER,
                },
            ),
            AuditAction.EXPORT_PUBLISH: _EXPORT_PUBLISH,
            AuditAction.EXPORT_REGENERATE: _EXPORT_PUBLISH,
            AuditAction.EXPORT_WRITE_COMMITTED: _EXPORT_PUBLISH,
            AuditAction.EXPORT_DELETE: ByDetail(
                "target_kind",
                {
                    "personal": ByAccount(
                        Capability.EXPORTS_PERSONAL_DELETE_OWN,
                        Capability.EXPORTS_PERSONAL_DELETE_OTHER,
                    ),
                    "item_default": Capability.EXPORTS_DEFAULT_DELETE,
                },
            ),
            AuditAction.EXPORT_RETAIN: ByDetail(
                "target_kind",
                {
                    "personal": ByAccount(
                        Capability.EXPORTS_PERSONAL_DOWNLOAD_OWN,
                        Capability.EXPORTS_PERSONAL_DELETE_OTHER,
                    ),
                    "item_default": Capability.EXPORTS_DEFAULT_PUBLISH,
                },
            ),
            AuditAction.COMMUNITY_PUBLISH: ByDetail(
                "kind",
                {
                    "own_review": ByAccount(
                        Capability.COMMUNITY_PUBLISH_OWN_REVIEW,
                        Capability.COMMUNITY_PUBLISH_OTHER_REVIEW,
                    ),
                    "other_review": ByAccount(
                        Capability.COMMUNITY_PUBLISH_OWN_REVIEW,
                        Capability.COMMUNITY_PUBLISH_OTHER_REVIEW,
                    ),
                    "scan_evidence": Capability.COMMUNITY_PUBLISH_SCAN_EVIDENCE,
                },
            ),
            AuditAction.COMMUNITY_RETRY: ByAccount(
                Capability.COMMUNITY_PUBLICATIONS_RETRY_OWN,
                Capability.COMMUNITY_PUBLICATIONS_RETRY_OTHER,
            ),
            AuditAction.COMMUNITY_RETRACT: ByAccount(
                Capability.COMMUNITY_PUBLICATIONS_RETRACT_OWN,
                Capability.COMMUNITY_PUBLICATIONS_RETRACT_OTHER,
            ),
            AuditAction.COMMUNITY_IDENTITY_CHANGE: ByDetail(
                "change",
                {
                    "enrollment_start": _IDENTITY_ENROLL,
                    "enrollment_complete": _IDENTITY_ENROLL,
                    "enrollment_cancel": _IDENTITY_ENROLL,
                    "disconnect": ByAccount(
                        Capability.COMMUNITY_IDENTITY_DISCONNECT_OWN,
                        Capability.COMMUNITY_IDENTITY_DISCONNECT_OTHER,
                    ),
                    "profile_rename": _IDENTITY_PROFILE,
                    "profile_rename_cancel": _IDENTITY_PROFILE,
                    "key_rotation": _IDENTITY_KEYS,
                    "key_rotation_retry": _IDENTITY_KEYS,
                    "key_recovery_start": _IDENTITY_KEYS,
                    "key_recovery_complete": _IDENTITY_KEYS,
                    "key_recovery_retry": _IDENTITY_KEYS,
                },
            ),
            AuditAction.CONNECTION_CHANGE: ByDetail(
                "change",
                {
                    "saved": Capability.CONNECTIONS_EDIT,
                    "disconnected": Capability.CONNECTIONS_DISCONNECT,
                },
            ),
            AuditAction.PATH_MAPPING_CHANGE: ByDetail(
                "change",
                {
                    "created": frozenset(
                        {Capability.PATH_MAPPINGS_CREATE, Capability.MEDIA_PATHS_VIEW}
                    ),
                    "updated": frozenset(
                        {Capability.PATH_MAPPINGS_UPDATE, Capability.MEDIA_PATHS_VIEW}
                    ),
                    "deleted": Capability.PATH_MAPPINGS_DELETE,
                    "reordered": frozenset(
                        {Capability.PATH_MAPPINGS_REORDER, Capability.MEDIA_PATHS_VIEW}
                    ),
                },
            ),
            AuditAction.DEPLOYMENT_CHANGE: Capability.DEPLOYMENT_EDIT,
            AuditAction.SCAN_SETTINGS_CHANGE: Capability.DEPLOYMENT_EDIT,
            AuditAction.LOGGING_CHANGE: Capability.LOGS_SETTINGS_EDIT,
            AuditAction.RESTART: Capability.SYSTEM_RESTART,
            AuditAction.BACKUP_CREATE: Capability.BACKUPS_CREATE,
            AuditAction.BACKUP_DOWNLOAD: Capability.BACKUPS_DOWNLOAD,
            # Restore happens before any account exists; only the owner's backup power reads it.
            AuditAction.BACKUP_RESTORE: Capability.BACKUPS_DOWNLOAD,
            AuditAction.BACKUP_DELETE: Capability.BACKUPS_DELETE,
            AuditAction.BACKUP_SCHEDULE_CHANGE: Capability.BACKUPS_SCHEDULE_EDIT,
            AuditAction.ACCOUNT_CREATE: ByDetail(
                "method",
                {
                    "managed": Capability.ACCOUNTS_JELLYFIN_IMPORT,
                    "jellyfin_import": Capability.ACCOUNTS_JELLYFIN_IMPORT,
                    "registration": None,
                    "jellyfin_first_login": None,
                },
            ),
            AuditAction.ACCOUNT_UPDATE: ByAccount(
                Capability.PROFILE_EDIT_OWN, Capability.ACCOUNTS_PROFILE_EDIT_OTHER
            ),
            AuditAction.ACCOUNT_STATUS: ByDetail(
                "status",
                {
                    "active": Capability.ACCOUNTS_ENABLE_OTHER,
                    "disabled": Capability.ACCOUNTS_DISABLE_OTHER,
                },
            ),
            AuditAction.ACCOUNT_PASSWORD_RESET: Capability.ACCOUNTS_SESSIONS_REVOKE_OTHER,
            AuditAction.PERMISSIONS_CHANGE: ByDetail(
                "change",
                {
                    "role": Capability.ROLES_ASSIGN,
                    "overrides": Capability.PERMISSIONS_OVERRIDES_EDIT,
                    "role_defaults": Capability.ROLES_DEFAULTS_EDIT,
                },
            ),
            AuditAction.MEDIA_ACCESS_CHANGE: ByDetail(
                "change",
                {
                    "default_scope": Capability.MEDIA_ACCESS_DEFAULTS_EDIT,
                    "user_scope": Capability.MEDIA_ACCESS_USER_EDIT,
                },
            ),
            AuditAction.REGISTRATION_CHANGE: Capability.CONNECTIONS_EDIT,
            AuditAction.JELLYFIN_ADMISSION_CHANGE: Capability.CONNECTIONS_EDIT,
            AuditAction.JELLYFIN_SYNC: Capability.ACCOUNTS_JELLYFIN_SYNC,
            AuditAction.JELLYFIN_ACCOUNT_STATE: Capability.ACCOUNTS_JELLYFIN_SYNC,
            AuditAction.JELLYFIN_UNBLOCK: Capability.ACCOUNTS_JELLYFIN_UNBLOCK,
            AuditAction.OWNERSHIP_TRANSFER: Capability.OWNERSHIP_TRANSFER,
            AuditAction.ACCOUNT_DELETE: Capability.ACCOUNTS_DELETE_OTHER,
            AuditAction.ACCOUNT_LINK: ByAccount(
                Capability.ACCOUNTS_LINK_OWN, Capability.ACCOUNTS_LINK_OTHER
            ),
        }
    )
)


def action_visible(
    action: AuditAction,
    target_user_id: int | None,
    details: Mapping[str, str | int | bool | None],
    access: PrincipalAccess,
) -> bool:
    """Check held capabilities, not mutation freshness or the historical actor's grants."""
    if access.principal.account.role is Role.OWNER:
        return True
    if action in {AuditAction.EXPORT_PUBLISH, AuditAction.EXPORT_REGENERATE}:
        source_owner = details.get("source_owner_id")
        if source_owner is not None:
            source_required = (
                {Capability.REVIEWS_VIEW_OWN}
                if source_owner == access.principal.user_id
                else {Capability.REVIEWS_VIEW_OTHER, Capability.REVIEWS_EXPORT_OTHER}
            )
            if not source_required <= access.capabilities:
                return False
    requirement = ACTION_CAPABILITIES.get(action)
    if isinstance(requirement, ByDetail):
        value = details.get(requirement.key)
        requirement = requirement.choices.get(value) if type(value) in (str, bool) else None
    if isinstance(requirement, ByAccount):
        if target_user_id is None:
            return False
        requirement = (
            requirement.own if target_user_id == access.principal.user_id else requirement.other
        )
    if requirement is None:
        return False
    required = frozenset({requirement}) if isinstance(requirement, Capability) else requirement
    return bool(required) and all(
        CAPABILITIES[capability].delegable and capability in access.capabilities
        for capability in required
    )
