"""Every action an audit row may name, with the actors, scope, and details it may carry.

The registry is the redaction rule: a detail key must be listed for its action,
and a string value must be one of the listed codes, so no free text, secret, or
user-supplied value can reach a stored row. The vocabulary it is written in lives
in `action_vocabulary` and is re-exported here; `checks.check_event` applies the
registry to one event.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.records import AuditEvent, AuditOutcome

from .action_vocabulary import COUNT as COUNT
from .action_vocabulary import FLAG as FLAG
from .action_vocabulary import ActionSpec as ActionSpec
from .action_vocabulary import ActorKind as ActorKind
from .action_vocabulary import AuditAction as AuditAction
from .action_vocabulary import AuditCategory as AuditCategory
from .action_vocabulary import AuditEventRejected as AuditEventRejected
from .action_vocabulary import DetailRule as DetailRule
from .action_vocabulary import Scope as Scope

_ACCESS_CODES = frozenset(code.value for code in AccessErrorCode)
# Development builds before MU-42 stored `multi_user_unavailable` sign-in refusals;
# the history still reads them.
_ADMISSION_CODES = frozenset({"multi_user_unavailable", AccessErrorCode.ACCOUNT_DISABLED.value})
_ALL_OUTCOMES = frozenset(AuditOutcome)
_USER = frozenset({ActorKind.USER})
_USER_OR_SYSTEM = frozenset({ActorKind.USER, ActorKind.SYSTEM})


def _every_outcome(actors: frozenset[ActorKind]) -> Mapping[AuditOutcome, frozenset[ActorKind]]:
    return {outcome: actors for outcome in _ALL_OUTCOMES}


def _export_target_matches(event: AuditEvent) -> bool:
    """A personal export names its account; the item default names none."""

    return (event.details["target_kind"] == "personal") == (event.target_user is not None)


def _export_write_matches(event: AuditEvent) -> bool:
    """A refusal for a publication that does not exist names only the ID and reason."""

    if event.media_key is None:
        return event.target_user is None and event.details.keys() == {"reason"}
    return {"format", "target_kind"} <= event.details.keys() and _export_target_matches(event)


_CREDENTIAL_CHANGE = {
    "changed": frozenset({"username", "password", "password_and_username"}),
    "sessions_revoked": COUNT,
}
_EXPORT = ActionSpec(
    AuditCategory.EXPORT,
    _every_outcome(_USER_OR_SYSTEM),
    Scope.OPTIONAL,
    Scope.REQUIRED,
    Scope.REQUIRED,
    {
        "format": frozenset({"edl", "mcf"}),
        "target_kind": frozenset({"personal", "item_default"}),
        "review_revision": COUNT,
        "policy_revision": COUNT,
        "source_owner_id": COUNT,
        "reason": _ACCESS_CODES | {"write_failed", "retained_path_absent"},
    },
    frozenset({"format", "target_kind"}),
    _export_target_matches,
)
EXPORT_WRITE_REFUSALS = frozenset(
    {
        "export_withdrawn",
        "jellyfin_name_unknown",
        "export_destination_changed",
        "export_destination_unavailable",
        "export_file_collision",
        "export_filename_collision",
        "export_recovery_required",
    }
)
_COMMUNITY = ActionSpec(
    AuditCategory.COMMUNITY,
    _every_outcome(_USER_OR_SYSTEM),
    Scope.REQUIRED,
    Scope.REQUIRED,
    Scope.REQUIRED,
    {
        "kind": frozenset({"own_review", "other_review", "scan_evidence"}),
        "reason": _ACCESS_CODES | {"service_unavailable", "service_rejected"},
    },
    frozenset({"kind"}),
)


def _settings(details: Mapping[str, DetailRule], resource: Scope = Scope.ABSENT) -> ActionSpec:
    """An instance settings change by a user; a refusal carries only its access code."""

    return ActionSpec(
        AuditCategory.SETTINGS,
        _every_outcome(_USER),
        Scope.ABSENT,
        Scope.ABSENT,
        resource,
        {**details, "reason": _ACCESS_CODES},
    )


_BACKUP_KIND = frozenset({"full", "settings"})
_LINK_REASONS = frozenset(
    {
        "account_proof_failed",
        "account_proof_unavailable",
        "jellyfin_identity_in_use",
        "jellyfin_proof_failed",
        "jellyfin_unavailable",
        "jellyfin_user_blocked",
        "jellyfin_user_unavailable",
        "link_proof_invalid",
        "link_proof_expired",
        "link_proof_stale",
        "link_preview_stale",
        "link_blocked",
        "write_failed",
    }
)
ACCOUNT_DELETE_REASONS = frozenset(
    {
        "stale_account_deletion",
        "account_deletion_blocked",
        "account_deletion_choice_required",
        "transfer_destination_unavailable",
        "review_deletion_choice_required",
        "stale_review_deletion",
        "export_changed",
        "export_conflict",
        "export_destination_changed",
        "export_destination_unavailable",
        "export_file_collision",
        "export_filename_collision",
        "export_io_failed",
        "export_recovery_required",
        "export_write_failed",
        "database_busy",
    }
)
"""Refusal reasons of a confirmed account deletion besides access codes; one per attempt."""
_SYNC_OUTCOMES = frozenset({"verified", "disabled", "deleted", "unverified", "server_replaced"})


def _account(
    details: Mapping[str, DetailRule],
    target: Scope = Scope.REQUIRED,
    reasons: frozenset[str] = frozenset(),
) -> ActionSpec:
    """An account management change by a user; a refusal carries only its access code."""

    return ActionSpec(
        AuditCategory.SECURITY,
        _every_outcome(_USER),
        target,
        Scope.ABSENT,
        Scope.ABSENT,
        {**details, "reason": _ACCESS_CODES | reasons},
    )


ACTION_SPECS: Mapping[AuditAction, ActionSpec] = MappingProxyType(
    {
        AuditAction.SETUP: ActionSpec(
            AuditCategory.SECURITY,
            {
                AuditOutcome.SUCCEEDED: _USER_OR_SYSTEM,
                AuditOutcome.FAILED: frozenset({ActorKind.ANONYMOUS, ActorKind.SYSTEM}),
            },
            Scope.OPTIONAL,
            Scope.ABSENT,
            Scope.ABSENT,
            {"reason": frozenset({"already_configured"})},
        ),
        AuditAction.SIGN_IN: ActionSpec(
            AuditCategory.SECURITY,
            {
                AuditOutcome.SUCCEEDED: _USER,
                AuditOutcome.FAILED: frozenset({ActorKind.ANONYMOUS}),
                AuditOutcome.DENIED: frozenset({ActorKind.ANONYMOUS}),
            },
            Scope.OPTIONAL,
            Scope.ABSENT,
            Scope.ABSENT,
            {
                "reason": _ADMISSION_CODES
                | {
                    "invalid_credentials",
                    "throttled",
                    "jellyfin_unavailable",
                    "jellyfin_admission_closed",
                    "jellyfin_user_blocked",
                },
                "remember_me": FLAG,
                "method": frozenset({"password", "quick_connect"}),
            },
        ),
        AuditAction.ACCOUNT_CHANGE: ActionSpec(
            AuditCategory.SECURITY,
            _every_outcome(_USER),
            Scope.REQUIRED,
            Scope.ABSENT,
            Scope.ABSENT,
            {
                **_CREDENTIAL_CHANGE,
                "reason": _ACCESS_CODES
                | {"invalid_current_password", "username_unavailable", "linked_profile_field"},
            },
        ),
        AuditAction.ACCOUNT_RECOVER: ActionSpec(
            AuditCategory.SECURITY,
            {AuditOutcome.SUCCEEDED: frozenset({ActorKind.SYSTEM})},
            Scope.REQUIRED,
            Scope.ABSENT,
            Scope.ABSENT,
            _CREDENTIAL_CHANGE,
        ),
        AuditAction.SESSIONS_REVOKE: ActionSpec(
            AuditCategory.SECURITY,
            {
                AuditOutcome.SUCCEEDED: _USER_OR_SYSTEM,
                AuditOutcome.DENIED: _USER,
                AuditOutcome.FAILED: _USER,
            },
            Scope.OPTIONAL,
            Scope.ABSENT,
            Scope.ABSENT,
            {"sessions_revoked": COUNT, "reason": _ACCESS_CODES | {"csrf_key_changed"}},
        ),
        AuditAction.API_KEY_ISSUE: ActionSpec(
            AuditCategory.SECURITY,
            _every_outcome(_USER),
            Scope.REQUIRED,
            Scope.ABSENT,
            Scope.ABSENT,
            {"replaced": FLAG, "reason": _ACCESS_CODES},
        ),
        AuditAction.API_KEY_REVOKE: ActionSpec(
            AuditCategory.SECURITY,
            _every_outcome(_USER),
            Scope.REQUIRED,
            Scope.ABSENT,
            Scope.ABSENT,
            {"revoked": FLAG, "reason": _ACCESS_CODES},
        ),
        AuditAction.API_KEY_REVEAL: ActionSpec(
            AuditCategory.SECURITY,
            _every_outcome(_USER),
            Scope.REQUIRED,
            Scope.ABSENT,
            Scope.ABSENT,
            {"reason": _ACCESS_CODES | {"api_key_not_configured", "api_key_unrecoverable"}},
        ),
        AuditAction.FILTER_POLICY_CHANGE: ActionSpec(
            AuditCategory.SECURITY,
            _every_outcome(_USER),
            Scope.REQUIRED,
            Scope.ABSENT,
            Scope.ABSENT,
            {
                "cause": frozenset({"enforcement", "instance_defaults", "ownership_transfer"}),
                "policy_revision": COUNT,
                "rules": COUNT,
                "severities": COUNT,
                "reason": _ACCESS_CODES,
            },
        ),
        AuditAction.EXPORT_PUBLISH: _EXPORT,
        AuditAction.EXPORT_REGENERATE: _EXPORT,
        AuditAction.EXPORT_DELETE: _EXPORT,
        AuditAction.EXPORT_RETAIN: _EXPORT,
        AuditAction.EXPORT_WRITE_COMMITTED: replace(
            _EXPORT,
            actors=_every_outcome(_USER),
            media=Scope.OPTIONAL,
            details=_EXPORT.details | {"reason": _EXPORT.details["reason"] | EXPORT_WRITE_REFUSALS},
            required_details=frozenset(),
            consistent=_export_write_matches,
        ),
        AuditAction.COMMUNITY_PUBLISH: _COMMUNITY,
        AuditAction.COMMUNITY_RETRY: _COMMUNITY,
        AuditAction.COMMUNITY_RETRACT: _COMMUNITY,
        AuditAction.COMMUNITY_IDENTITY_CHANGE: ActionSpec(
            AuditCategory.COMMUNITY,
            _every_outcome(_USER),
            Scope.REQUIRED,
            Scope.ABSENT,
            Scope.ABSENT,
            {
                "change": frozenset(
                    {
                        "enrollment_start",
                        "enrollment_complete",
                        "enrollment_cancel",
                        "disconnect",
                        "profile_rename",
                        "profile_rename_cancel",
                        "key_rotation",
                        "key_rotation_retry",
                        "key_recovery_start",
                        "key_recovery_complete",
                        "key_recovery_retry",
                    }
                ),
                "reason": _ACCESS_CODES
                | {"service_unavailable", "service_rejected", "identity_unavailable"},
            },
        ),
        AuditAction.CONNECTION_CHANGE: _settings(
            {
                # Radarr and Sonarr stay so their recorded history still reads.
                "service": frozenset({"jellyfin", "radarr", "sonarr"}),
                "change": frozenset({"saved", "disconnected"}),
            }
        ),
        AuditAction.PATH_MAPPING_CHANGE: _settings(
            {"change": frozenset({"created", "updated", "deleted", "reordered"})},
            Scope.OPTIONAL,
        ),
        AuditAction.DEPLOYMENT_CHANGE: _settings({"base_url": FLAG, "port": FLAG}),
        AuditAction.SCAN_SETTINGS_CHANGE: _settings({}),
        AuditAction.LOGGING_CHANGE: _settings({"debug_enabled": FLAG}),
        AuditAction.RESTART: _settings({}),
        AuditAction.BACKUP_CREATE: _settings(
            {"kind": _BACKUP_KIND, "encrypted": FLAG}, Scope.OPTIONAL
        ),
        AuditAction.BACKUP_DOWNLOAD: _settings({"kind": _BACKUP_KIND}, Scope.OPTIONAL),
        # Settings restores (stages start to activate) left rows before restore moved to
        # onboarding; a restore now records one system `restored` event in its own database.
        AuditAction.BACKUP_RESTORE: replace(
            _settings(
                {
                    "stage": frozenset({"start", "upload", "preflight", "activate", "restored"}),
                    "move_disagreements": COUNT,
                },
                Scope.OPTIONAL,
            ),
            actors={**_every_outcome(_USER), AuditOutcome.SUCCEEDED: _USER_OR_SYSTEM},
        ),
        AuditAction.BACKUP_DELETE: _settings({}, Scope.OPTIONAL),
        AuditAction.BACKUP_SCHEDULE_CHANGE: _settings(
            {"change": frozenset({"configured", "disabled"})}
        ),
        AuditAction.ACCOUNT_CREATE: ActionSpec(
            AuditCategory.SECURITY,
            {
                AuditOutcome.SUCCEEDED: frozenset({ActorKind.USER, ActorKind.ANONYMOUS}),
                AuditOutcome.DENIED: _USER,
                AuditOutcome.FAILED: _USER,
            },
            Scope.OPTIONAL,
            Scope.ABSENT,
            Scope.ABSENT,
            {
                "method": frozenset(
                    {"managed", "registration", "jellyfin_import", "jellyfin_first_login"}
                ),
                "reason": _ACCESS_CODES | {"jellyfin_user_blocked"},
            },
        ),
        AuditAction.ACCOUNT_UPDATE: _account(
            {
                "change": frozenset(
                    {"profile", "username", "profile_and_username", "avatar_set", "avatar_removed"}
                ),
                "sessions_revoked": COUNT,
            }
        ),
        AuditAction.ACCOUNT_STATUS: _account(
            {"status": frozenset({"active", "disabled"}), "sessions_revoked": COUNT}
        ),
        AuditAction.ACCOUNT_PASSWORD_RESET: _account({"sessions_revoked": COUNT}),
        AuditAction.PERMISSIONS_CHANGE: _account(
            {
                "change": frozenset({"role", "overrides", "role_defaults"}),
                "role": frozenset({"admin", "user"}),
                "changed": COUNT,
            },
            Scope.OPTIONAL,
            frozenset({"local_account", "jellyfin_state_unverified"}),
        ),
        AuditAction.MEDIA_ACCESS_CHANGE: _account(
            {
                "change": frozenset(
                    {"default_scope", "user_scope", "exception_granted", "exception_revoked"}
                )
            },
            Scope.OPTIONAL,
        ),
        AuditAction.REGISTRATION_CHANGE: _settings({"mode": frozenset({"managed", "open"})}),
        # Owners chose a mode before every first sign-in created an account; migration
        # 0028 records one system event where it turned import-only off.
        AuditAction.JELLYFIN_ADMISSION_CHANGE: replace(
            _settings(
                {
                    "mode": frozenset({"first_login", "import_only"}),
                    "previous": frozenset({"import_only"}),
                }
            ),
            actors={**_every_outcome(_USER), AuditOutcome.SUCCEEDED: _USER_OR_SYSTEM},
        ),
        AuditAction.JELLYFIN_SYNC: _account(
            {outcome: COUNT for outcome in _SYNC_OUTCOMES} | {"accounts": COUNT}, Scope.ABSENT
        ),
        AuditAction.JELLYFIN_ACCOUNT_STATE: ActionSpec(
            AuditCategory.SECURITY,
            {AuditOutcome.SUCCEEDED: frozenset({ActorKind.SYSTEM})},
            Scope.REQUIRED,
            Scope.ABSENT,
            Scope.ABSENT,
            {
                "outcome": _SYNC_OUTCOMES,
                "role": frozenset({"admin", "user"}),
                "disabled": FLAG,
                "enabled": FLAG,
                "sessions_revoked": COUNT,
                "username": frozenset({"refreshed", "unavailable"}),
            },
        ),
        AuditAction.JELLYFIN_UNBLOCK: ActionSpec(
            AuditCategory.SECURITY,
            _every_outcome(_USER),
            Scope.ABSENT,
            Scope.ABSENT,
            Scope.OPTIONAL,
            {"reason": _ACCESS_CODES | {"jellyfin_user_not_blocked"}},
        ),
        AuditAction.ACCOUNT_LINK: ActionSpec(
            AuditCategory.SECURITY,
            _every_outcome(_USER_OR_SYSTEM),
            Scope.REQUIRED,
            Scope.ABSENT,
            Scope.ABSENT,
            {
                "step": frozenset({"proof", "link", "recovery"}),
                "basis": frozenset({"account_password", "actor_authentication", "admin_attested"}),
                "kind": frozenset({"link", "relink"}),
                "state": frozenset({"removing", "completed", "rolled_back"}),
                "exports": COUNT,
                "role": frozenset({"owner", "admin", "user"}),
                "reason": _ACCESS_CODES | _LINK_REASONS,
            },
        ),
        AuditAction.ACCOUNT_DELETE: _account(
            {
                "mode": frozenset({"retain", "transfer", "delete"}),
                "destination_id": COUNT,
                "reviews": COUNT,
                "files_retained": COUNT,
                "files_removed": COUNT,
                "files_moved": COUNT,
                "policy_files": COUNT,
                "scans_deleted": COUNT,
                "scans_kept": COUNT,
                "sessions_revoked": COUNT,
                "key_revoked": FLAG,
                "identity_released": FLAG,
                "jellyfin_blocked": FLAG,
            },
            Scope.REQUIRED,
            ACCOUNT_DELETE_REASONS,
        ),
        AuditAction.OWNERSHIP_TRANSFER: ActionSpec(
            AuditCategory.SECURITY,
            _every_outcome(_USER),
            Scope.REQUIRED,
            Scope.ABSENT,
            Scope.ABSENT,
            {
                "successor_role": frozenset({"admin", "user"}),
                "reason": _ACCESS_CODES | {"successor_disabled", "successor_credentials_required"},
            },
        ),
    }
)
