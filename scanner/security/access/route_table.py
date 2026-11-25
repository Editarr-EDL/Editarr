"""The registered route/action table for the current application.

Each rule names what a route may exercise. A gated handler resolves its
principal through `request_access` and decides each target it touches.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .capability_ids import Capability as C
from .routes import AnyPrincipal, Gated, PrincipalReason, Public, PublicReason, RouteKey, RouteRule


def _gated(*capabilities: C) -> Gated:
    return Gated(frozenset(capabilities))


_ITEM_EXPORTS = (C.EXPORTS_PERSONAL_DOWNLOAD_OWN, C.EXPORTS_PERSONAL_DELETE_OTHER)
_REVIEW_VIEW = (C.REVIEWS_VIEW_OWN, C.REVIEWS_VIEW_OTHER)
_PUBLISH_EXPORT = _gated(
    C.EXPORTS_PERSONAL_PUBLISH_OWN, C.REVIEWS_EXPORT_OTHER, C.EXPORTS_DEFAULT_PUBLISH
)
_SUBTITLE_EXPORT = _gated(
    *_REVIEW_VIEW,
    C.EXPORTS_PERSONAL_DOWNLOAD_OWN,
    C.EXPORTS_PERSONAL_PUBLISH_OWN,
    C.REVIEWS_EXPORT_OTHER,
    C.EXPORTS_DEFAULT_PUBLISH,
)
_PREVIEW = _gated(C.REVIEWS_PREVIEW_OWN, C.REVIEWS_PREVIEW_OTHER)
_JOB_VIEW = (C.SCAN_JOBS_VIEW_OWN, C.SCAN_JOBS_VIEW_OTHER)
# A scan is visible as its job or, once complete, as shared findings on its media.
_SEEN = (*_JOB_VIEW, C.SCANS_FINDINGS_READ)
_IDENTITY_VIEW = _gated(C.COMMUNITY_IDENTITY_VIEW_OWN, C.COMMUNITY_IDENTITY_VIEW_OTHER)
_IDENTITY_ENROLL = _gated(C.COMMUNITY_IDENTITY_ENROLL_OWN, C.COMMUNITY_IDENTITY_ENROLL_OTHER)
_IDENTITY_PROFILE = _gated(C.COMMUNITY_IDENTITY_PROFILE_OWN, C.COMMUNITY_IDENTITY_PROFILE_OTHER)
_IDENTITY_KEYS = _gated(C.COMMUNITY_IDENTITY_KEYS_OWN, C.COMMUNITY_IDENTITY_KEYS_OTHER)
_IDENTITY_DISCONNECT = _gated(
    C.COMMUNITY_IDENTITY_DISCONNECT_OWN, C.COMMUNITY_IDENTITY_DISCONNECT_OTHER
)
_SHARE = _gated(
    C.COMMUNITY_PUBLISH_OWN_REVIEW,
    C.COMMUNITY_PUBLISH_OTHER_REVIEW,
    C.COMMUNITY_PUBLISH_SCAN_EVIDENCE,
)
_SEARCH = _gated(C.COMMUNITY_SEARCH)
_DOWNLOAD = _gated(C.COMMUNITY_DOWNLOAD)
_FEEDBACK = _gated(C.COMMUNITY_FEEDBACK_SUBMIT)
_PUBLICATION_CHECK = _gated(
    C.COMMUNITY_PUBLICATIONS_CHECK_OWN, C.COMMUNITY_PUBLICATIONS_CHECK_OTHER
)
_SHARE_CONTEXT = _gated(
    C.COMMUNITY_PUBLISH_OWN_REVIEW,
    C.COMMUNITY_PUBLISH_OTHER_REVIEW,
    C.COMMUNITY_PUBLISH_SCAN_EVIDENCE,
    C.COMMUNITY_PUBLICATIONS_CHECK_OWN,
    C.COMMUNITY_PUBLICATIONS_CHECK_OTHER,
)
_SAVE_REVIEW = _gated(
    C.REVIEWS_SAVE_OWN,
    C.REVIEWS_EDIT_OTHER,
    C.REVIEWS_EVIDENCE_EDIT_OWN,
    C.REVIEWS_EVIDENCE_EDIT_OTHER,
    C.REVIEWS_FILTERS_EDIT_OWN,
    C.REVIEWS_FILTERS_EDIT_OTHER,
    C.REVIEWS_CUES_EDIT_OWN,
    C.REVIEWS_CUES_EDIT_OTHER,
    C.REVIEWS_METADATA_EDIT_OWN,
    C.REVIEWS_METADATA_EDIT_OTHER,
    C.REVIEWS_TIMING_EDIT_OWN,
    C.REVIEWS_TIMING_EDIT_OTHER,
    C.REVIEWS_CREDITS_EDIT_OWN,
    C.REVIEWS_CREDITS_EDIT_OTHER,
)
_REPAIR_REVIEW = _gated(C.REVIEWS_SAVE_OWN, C.REVIEWS_EDIT_OTHER)
_DOCS = AnyPrincipal(PrincipalReason.API_DOCUMENTATION)
_REFERENCE = AnyPrincipal(PrincipalReason.REFERENCE_DATA)
_SETUP_RESTORE = Public(PublicReason.SETUP_RESTORE)
_BROWSE = _gated(C.MEDIA_BROWSE)
_FINDINGS = _gated(C.SCANS_FINDINGS_READ)
# A source path narrows the lookup only for a caller who may see server paths.
# Adjacent pre-migration sidecars are shared item files, deleted like an item default.
_LEGACY_DELETE = _gated(C.EXPORTS_DEFAULT_DELETE)
_START = _gated(C.SCANS_START)
_VIEW_ACCOUNT = _gated(C.PROFILE_VIEW_OWN, C.ACCOUNTS_LIST)
_EDIT_PROFILE = _gated(C.PROFILE_EDIT_OWN, C.ACCOUNTS_PROFILE_EDIT_OTHER)

# Each entry is "METHODS path" and its rule. Methods are space-separated.
_ENTRIES: tuple[tuple[str, RouteRule], ...] = (
    ("POST /api/reviews/{review_id}/repair/recover", _REPAIR_REVIEW),
    ("POST /api/reviews/{review_id}/repair/community", _REPAIR_REVIEW),
    ("DELETE /api/community/connection", _IDENTITY_DISCONNECT),
    ("DELETE /api/users/{user_id}/community/connection", _IDENTITY_DISCONNECT),
    ("GET /api/auth/status", Public(PublicReason.STATUS)),
    ("GET /api/audit", _gated(C.AUDIT_READ)),
    ("GET /api/audit/{event_id}", _gated(C.AUDIT_READ)),
    ("GET /api/archive", _gated(C.ARCHIVE_VIEW)),
    ("POST /api/auth/setup", Public(PublicReason.SETUP)),
    ("POST /api/auth/logout", AnyPrincipal(PrincipalReason.OWN_SESSION)),
    ("GET /api/auth/api-key", _gated(C.PROFILE_VIEW_OWN)),
    ("POST /api/auth/api-key", _gated(C.API_KEYS_CREATE_OWN, C.API_KEYS_ROTATE_OWN)),
    ("POST /api/auth/api-key/reveal", _gated(C.API_KEYS_REVEAL_OWN)),
    ("DELETE /api/auth/api-key", _gated(C.API_KEYS_REVOKE_OWN)),
    (
        "POST /api/users/{user_id}/api-key",
        _gated(C.API_KEYS_CREATE_OWN, C.API_KEYS_ROTATE_OWN, C.API_KEYS_ISSUE_OTHER),
    ),
    (
        "POST /api/users/{user_id}/api-key/reveal",
        _gated(C.API_KEYS_REVEAL_OWN, C.API_KEYS_REVEAL_OTHER),
    ),
    (
        "DELETE /api/users/{user_id}/api-key",
        _gated(C.API_KEYS_REVOKE_OWN, C.API_KEYS_REVOKE_OTHER),
    ),
    ("POST /api/auth/jellyfin/login", Public(PublicReason.SIGN_IN)),
    ("GET /api/auth/jellyfin/quick-connect", Public(PublicReason.SIGN_IN)),
    ("POST /api/auth/jellyfin/quick-connect/initiate", Public(PublicReason.SIGN_IN)),
    ("GET /api/auth/jellyfin/quick-connect/status", Public(PublicReason.SIGN_IN)),
    ("POST /api/auth/jellyfin/quick-connect/complete", Public(PublicReason.SIGN_IN)),
    ("POST /api/auth/jellyfin/quick-connect/cancel", Public(PublicReason.SIGN_IN)),
    ("GET /api/jellyfin/users", _gated(C.ACCOUNTS_JELLYFIN_IMPORT)),
    ("POST /api/jellyfin/users/import", _gated(C.ACCOUNTS_JELLYFIN_IMPORT)),
    ("GET /api/jellyfin/sync", _gated(C.ACCOUNTS_JELLYFIN_SYNC)),
    ("POST /api/jellyfin/sync", _gated(C.ACCOUNTS_JELLYFIN_SYNC)),
    ("GET /api/jellyfin/blocks", _gated(C.ACCOUNTS_LIST)),
    ("POST /api/jellyfin/blocks/unblock", _gated(C.ACCOUNTS_JELLYFIN_UNBLOCK)),
    ("GET /api/users", _gated(C.ACCOUNTS_LIST)),
    (
        "POST /api/users/bulk",
        _gated(
            C.ACCOUNTS_DISABLE_OTHER,
            C.ACCOUNTS_ENABLE_OTHER,
            C.ACCOUNTS_SESSIONS_REVOKE_OTHER,
            C.ROLES_ASSIGN,
            C.MEDIA_ACCESS_USER_EDIT,
        ),
    ),
    ("GET /api/users/{user_id}", _VIEW_ACCOUNT),
    ("PATCH /api/users/{user_id}", _EDIT_PROFILE),
    ("GET /api/users/{user_id}/avatar", _VIEW_ACCOUNT),
    ("PUT DELETE /api/users/{user_id}/avatar", _EDIT_PROFILE),
    ("PUT /api/users/{user_id}/status", _gated(C.ACCOUNTS_DISABLE_OTHER, C.ACCOUNTS_ENABLE_OTHER)),
    ("DELETE /api/users/{user_id}/sessions", _gated(C.ACCOUNTS_SESSIONS_REVOKE_OTHER)),
    ("PUT /api/users/{user_id}/role", _gated(C.ROLES_ASSIGN)),
    ("POST /api/users/{user_id}/link/proof", _gated(C.ACCOUNTS_LINK_OWN, C.ACCOUNTS_LINK_OTHER)),
    ("POST /api/users/{user_id}/link", _gated(C.ACCOUNTS_LINK_OWN, C.ACCOUNTS_LINK_OTHER)),
    ("GET POST /api/users/{user_id}/deletion", _gated(C.ACCOUNTS_DELETE_OTHER)),
    ("POST /api/users/bulk-deletion", _gated(C.ACCOUNTS_DELETE_OTHER)),
    ("POST /api/users/{user_id}/ownership", _gated(C.OWNERSHIP_TRANSFER)),
    ("GET PATCH /api/users/{user_id}/permissions", _gated(C.PERMISSIONS_OVERRIDES_EDIT)),
    ("GET /api/roles/defaults", _gated(C.ROLES_DEFAULTS_EDIT)),
    ("PATCH /api/roles/{role}/defaults", _gated(C.ROLES_DEFAULTS_EDIT)),
    ("GET PUT /api/media-access/default", _gated(C.MEDIA_ACCESS_DEFAULTS_EDIT)),
    ("GET /api/users/{user_id}/media-access", _gated(C.MEDIA_ACCESS_USER_EDIT)),
    ("PUT /api/users/{user_id}/media-access", _gated(C.MEDIA_ACCESS_USER_EDIT)),
    ("POST /api/setup-restore/flow", _SETUP_RESTORE),
    ("POST /api/setup-restore/jobs", _SETUP_RESTORE),
    ("GET DELETE /api/setup-restore/jobs/{job_id}", _SETUP_RESTORE),
    ("PUT /api/setup-restore/jobs/{job_id}/archive", _SETUP_RESTORE),
    ("POST /api/setup-restore/jobs/{job_id}/preflight", _SETUP_RESTORE),
    ("POST /api/setup-restore/jobs/{job_id}/activate", _SETUP_RESTORE),
    ("GET HEAD /openapi.json", _DOCS),
    ("GET HEAD /docs", _DOCS),
    ("GET HEAD /docs/oauth2-redirect", _DOCS),
    ("GET HEAD /redoc", _DOCS),
    ("GET /api/mcf-registry", _REFERENCE),
    # Every account inherits the instance filter defaults live, so reading them is shared.
    ("GET /api/settings/filters", _REFERENCE),
    ("PUT /api/settings/filters", _gated(C.FILTERS_INSTANCE_DEFAULTS_EDIT)),
    (
        "POST /api/settings/filters/apply",
        _gated(C.REVIEWS_FILTERS_EDIT_OWN, C.REVIEWS_FILTERS_EDIT_OTHER),
    ),
    # Every account reads its own effective defaults and the mode it may inherit.
    ("GET /api/settings/filters/default-mode", _REFERENCE),
    ("PUT /api/settings/filters/default-mode", _gated(C.FILTERS_INSTANCE_MODE_EDIT)),
    ("GET /api/settings/filters/personal", _REFERENCE),
    ("PUT /api/settings/filters/personal", _gated(C.FILTERS_PREFERENCES_EDIT_OWN)),
    ("PUT /api/settings/filters/personal/mode", _gated(C.FILTERS_MODE_CHOOSE_OWN)),
    (
        "GET /api/settings/filters/users/{user_id}",
        _gated(
            C.FILTERS_PREFERENCES_EDIT_OWN,
            C.FILTERS_PREFERENCES_EDIT_OTHER,
            C.FILTERS_MODE_CHOOSE_OWN,
            C.FILTERS_MODE_ASSIGN_OTHER,
            C.FILTERS_POLICY_ENFORCE,
        ),
    ),
    (
        "PUT /api/settings/filters/users/{user_id}",
        _gated(C.FILTERS_PREFERENCES_EDIT_OWN, C.FILTERS_PREFERENCES_EDIT_OTHER),
    ),
    (
        "PUT /api/settings/filters/users/{user_id}/mode",
        _gated(C.FILTERS_MODE_CHOOSE_OWN, C.FILTERS_MODE_ASSIGN_OTHER),
    ),
    ("PUT /api/settings/filters/users/{user_id}/policy", _gated(C.FILTERS_POLICY_ENFORCE)),
    ("GET /api/system/version", _gated(C.SYSTEM_VERSION_VIEW)),
    ("GET POST /api/system/restart", _gated(C.SYSTEM_RESTART)),
    ("GET /api/system/logs/settings", _gated(C.LOGS_VIEW)),
    ("PUT /api/system/logs/settings", _gated(C.LOGS_SETTINGS_EDIT)),
    ("GET /api/system/logs/{channel}", _gated(C.LOGS_VIEW)),
    ("GET /api/settings/deployment", _gated(C.DEPLOYMENT_VIEW)),
    ("PUT /api/settings/deployment", _gated(C.DEPLOYMENT_EDIT)),
    ("GET /api/settings/macos/dock", _gated(C.DEPLOYMENT_VIEW)),
    ("PUT /api/settings/macos/dock", _gated(C.DEPLOYMENT_EDIT)),
    ("GET /api/settings/jellyfin", _gated(C.CONNECTIONS_VIEW)),
    ("PUT /api/settings/jellyfin", _gated(C.CONNECTIONS_EDIT)),
    ("DELETE /api/settings/jellyfin", _gated(C.CONNECTIONS_DISCONNECT)),
    ("POST /api/settings/jellyfin/test", _gated(C.CONNECTIONS_TEST)),
    ("GET /api/settings/path-mappings", _gated(C.PATH_MAPPINGS_VIEW)),
    ("POST /api/settings/path-mappings", _gated(C.PATH_MAPPINGS_CREATE)),
    ("PATCH /api/settings/path-mappings/{mapping_id}", _gated(C.PATH_MAPPINGS_UPDATE)),
    ("DELETE /api/settings/path-mappings/{mapping_id}", _gated(C.PATH_MAPPINGS_DELETE)),
    ("POST /api/settings/path-mappings/reorder", _gated(C.PATH_MAPPINGS_REORDER)),
    ("POST /api/settings/path-mappings/resolve", _gated(C.PATH_MAPPINGS_TEST)),
    ("GET /api/models", _gated(C.MODELS_INVENTORY_VIEW)),
    ("GET /api/settings/models/scans", _gated(C.MODELS_INVENTORY_VIEW)),
    ("PUT /api/settings/models/scans", _gated(C.DEPLOYMENT_EDIT)),
    ("POST /api/models/prefetch", _gated(C.MODELS_DOWNLOAD)),
    ("POST /api/models/{artifact_id}/cancel", _gated(C.MODELS_DOWNLOAD_CANCEL)),
    ("DELETE /api/models/{artifact_id}", _gated(C.MODELS_DELETE)),
    ("POST /api/backups/jobs", _gated(C.BACKUPS_CREATE)),
    ("GET /api/backups/jobs/{job_id}", _gated(C.BACKUPS_STATUS_VIEW)),
    ("DELETE /api/backups/jobs/{job_id}", _gated(C.BACKUPS_DELETE)),
    ("GET HEAD /api/backups/jobs/{job_id}/download", _gated(C.BACKUPS_DOWNLOAD)),
    ("GET /api/backups/schedule", _gated(C.BACKUPS_STATUS_VIEW)),
    ("PUT DELETE /api/backups/schedule", _gated(C.BACKUPS_SCHEDULE_EDIT)),
    ("GET /api/capabilities", _START),
    ("GET /api/libraries", _BROWSE),
    ("GET /api/libraries/{library_id}/items", _BROWSE),
    ("GET /api/editarr-items/{title_id}", _BROWSE),
    ("GET /api/editarr-items/legacy/{item_id}", _BROWSE),
    ("POST /api/editarr-items/{title_id}/versions", _BROWSE),
    ("GET /api/media-fingerprints/queue", _gated(C.REVIEWS_IMPORT_UPLOAD)),
    (
        "POST /api/media-versions/{version_id}/reuse/transcript-status",
        _gated(C.REVIEWS_IMPORT_UPLOAD),
    ),
    ("POST /api/media-versions/{version_id}/reuse/preview", _gated(C.REVIEWS_IMPORT_UPLOAD)),
    ("POST /api/media-versions/{version_id}/reuse/approve", _gated(C.REVIEWS_IMPORT_UPLOAD)),
    ("GET /api/library-items/{item_id}", _BROWSE),
    ("GET /api/library-items/{item_id}/children", _BROWSE),
    ("GET /api/library-items/{item_id}/seasons", _BROWSE),
    ("GET /api/library-items/{item_id}/image", _BROWSE),
    ("GET /api/media/subtitles/publications", _BROWSE),
    ("GET /api/library-items/{item_id}/scans", _gated(*_SEEN)),
    ("GET /api/library-items/{item_id}/exports", _gated(*_ITEM_EXPORTS)),
    ("DELETE /api/library-items/{item_id}/exports/{format}", _LEGACY_DELETE),
    ("GET /api/library-items/{item_id}/reviews", _gated(*_REVIEW_VIEW)),
    ("POST /api/library-items/{item_id}/reviews", _gated(C.REVIEWS_CREATE_OWN)),
    ("GET /api/media/audio-tracks", _gated(C.SCANS_START)),
    ("POST /api/media/audio-tracks/selection", _gated(C.SCANS_START)),
    ("POST /api/filter-candidates", _FINDINGS),
    ("POST /api/filter-candidates/available", _FINDINGS),
    ("POST /api/filter-candidates/associations", _gated(C.MEDIA_IDENTITY_EDIT)),
    ("POST /api/scans", _START),
    ("GET /api/scans/history", _gated(*_SEEN)),
    ("GET /api/scans/queue", _gated(*_SEEN, C.MODELS_PROGRESS_VIEW_OWN)),
    (
        "POST /api/scans/queue/reorder",
        _gated(C.SCAN_JOBS_REORDER, *_SEEN, C.MODELS_PROGRESS_VIEW_OWN),
    ),
    (
        "DELETE /api/scans/queue/{queue_id}",
        _gated(C.SCAN_JOBS_CANCEL_OWN, C.SCAN_JOBS_CANCEL_OTHER, *_SEEN),
    ),
    (
        "POST /api/scans/queue/{queue_id}/resume",
        _gated(C.SCAN_JOBS_RESUME_OWN, C.SCAN_JOBS_RESUME_OTHER, *_SEEN),
    ),
    ("PATCH /api/scans/{scan_id}", _gated(C.SCANS_RENAME_OWN, C.SCANS_RENAME_OTHER, *_SEEN)),
    (
        "DELETE /api/scans/{scan_id}",
        _gated(C.SCANS_DELETE_OWN, C.SCANS_DELETE_OTHER, *_SEEN, *_REVIEW_VIEW),
    ),
    ("GET /api/scans/{scan_id}/findings", _gated(*_SEEN)),
    # Reference checks guard deletion; hidden reviews block without being disclosed.
    (
        "GET /api/scans/{scan_id}/references",
        _gated(C.SCANS_DELETE_OWN, C.SCANS_DELETE_OTHER, *_SEEN, *_REVIEW_VIEW),
    ),
    # Credits detection is a scan job; accepting its candidate edits a review.
    ("POST /api/credits-detections", _gated(C.SCANS_START)),
    ("GET /api/credits-detections/{scan_id}", _gated(*_SEEN)),
    (
        "POST /api/credits-detections/{scan_id}/model-consent",
        _gated(C.MODELS_DOWNLOAD, C.SCAN_JOBS_RESUME_OWN, C.SCAN_JOBS_RESUME_OTHER, *_SEEN),
    ),
    (
        "POST /api/credits-detections/{scan_id}/accept",
        _gated(C.REVIEWS_CREDITS_EDIT_OWN, C.REVIEWS_CREDITS_EDIT_OTHER),
    ),
    ("GET /api/credits-detections/{scan_id}/clip", _PREVIEW),
    ("GET /api/reviews/{review_id}", _gated(*_REVIEW_VIEW)),
    ("PUT /api/reviews/{review_id}", _SAVE_REVIEW),
    ("PATCH /api/reviews/{review_id}", _gated(C.REVIEWS_RENAME_OWN, C.REVIEWS_EDIT_OTHER)),
    ("DELETE /api/reviews/{review_id}", _gated(C.REVIEWS_DELETE_OWN, C.REVIEWS_DELETE_OTHER)),
    (
        "GET POST /api/reviews/{review_id}/deletion",
        _gated(
            C.REVIEWS_DELETE_OWN,
            C.REVIEWS_DELETE_OTHER,
            C.EXPORTS_PERSONAL_DOWNLOAD_OWN,
            C.EXPORTS_PERSONAL_DELETE_OWN,
            C.EXPORTS_PERSONAL_DELETE_OTHER,
            C.EXPORTS_DEFAULT_PUBLISH,
            C.EXPORTS_DEFAULT_DELETE,
        ),
    ),
    (
        "POST /api/reviews/{review_id}/duplicate",
        _gated(C.REVIEWS_DUPLICATE_OWN, C.REVIEWS_COPY_OTHER),
    ),
    (
        "POST /api/reviews/{review_id}/retime",
        _gated(C.REVIEWS_TIMING_EDIT_OWN, C.REVIEWS_TIMING_EDIT_OTHER),
    ),
    ("POST /api/library-items/{item_id}/reviews/preview", _PREVIEW),
    ("GET /api/reviews/{review_id}/filters/{filter_id}/mute-preview", _PREVIEW),
    ("GET /api/reviews/{review_id}/filters/{filter_id}/video-preview", _PREVIEW),
    ("POST /api/reviews/{review_id}/edl", _PUBLISH_EXPORT),
    ("POST /api/reviews/{review_id}/mcf", _PUBLISH_EXPORT),
    ("POST /api/reviews/{review_id}/subtitles/sources", _SUBTITLE_EXPORT),
    ("POST /api/reviews/{review_id}/subtitles/preview", _SUBTITLE_EXPORT),
    ("POST /api/reviews/{review_id}/subtitles/publish", _SUBTITLE_EXPORT),
    ("POST /api/reviews/{review_id}/exports/{format}", _PUBLISH_EXPORT),
    (
        "POST /api/reviews/{review_id}/exports/{format}/download",
        _gated(C.EXPORTS_PERSONAL_DOWNLOAD_OWN, C.REVIEWS_EXPORT_OTHER),
    ),
    (
        "GET /api/reviews/{review_id}/exports",
        _gated(
            *_REVIEW_VIEW,
            C.EXPORTS_PERSONAL_DOWNLOAD_OWN,
            C.EXPORTS_PERSONAL_DELETE_OTHER,
            C.EXPORTS_DEFAULT_PUBLISH,
        ),
    ),
    (
        "GET /api/exports",
        _gated(
            C.EXPORTS_PERSONAL_DOWNLOAD_OWN,
            C.EXPORTS_PERSONAL_DELETE_OTHER,
            C.EXPORTS_DEFAULT_PUBLISH,
        ),
    ),
    (
        "GET /api/exports/{export_id}",
        _gated(
            C.EXPORTS_PERSONAL_DOWNLOAD_OWN,
            C.EXPORTS_PERSONAL_DELETE_OTHER,
            C.EXPORTS_DEFAULT_PUBLISH,
        ),
    ),
    (
        "DELETE /api/exports/{export_id}",
        _gated(
            C.EXPORTS_PERSONAL_DELETE_OWN, C.EXPORTS_PERSONAL_DELETE_OTHER, C.EXPORTS_DEFAULT_DELETE
        ),
    ),
    ("GET /api/reviews/{review_id}/mcf/preflight", _gated(*_REVIEW_VIEW)),
    ("POST /api/edl-imports/upload", _gated(C.REVIEWS_IMPORT_UPLOAD)),
    ("POST /api/mcf-imports/upload", _gated(C.REVIEWS_IMPORT_UPLOAD)),
    ("GET /api/community/connection", _IDENTITY_VIEW),
    ("GET /api/community/connection/status", _IDENTITY_VIEW),
    ("GET /api/community/profile", _IDENTITY_VIEW),
    ("GET /api/community/keys/recovery", _IDENTITY_VIEW),
    ("POST /api/community/enrollment/start", _IDENTITY_ENROLL),
    ("POST /api/community/enrollment/complete", _IDENTITY_ENROLL),
    ("DELETE /api/community/enrollment", _IDENTITY_ENROLL),
    ("PATCH DELETE /api/community/profile", _IDENTITY_PROFILE),
    ("POST /api/community/keys/rotate", _IDENTITY_KEYS),
    ("POST /api/community/keys/rotation-status", _IDENTITY_KEYS),
    ("POST /api/community/keys/rotation-retry", _IDENTITY_KEYS),
    ("POST /api/community/keys/recovery/start", _IDENTITY_KEYS),
    ("POST /api/community/keys/recovery/complete", _IDENTITY_KEYS),
    ("POST /api/community/keys/recovery/check", _IDENTITY_KEYS),
    ("POST /api/community/keys/recovery/retry", _IDENTITY_KEYS),
    ("GET /api/users/{user_id}/community/connection", _IDENTITY_VIEW),
    ("GET /api/users/{user_id}/community/connection/status", _IDENTITY_VIEW),
    ("GET /api/users/{user_id}/community/profile", _IDENTITY_VIEW),
    ("GET /api/users/{user_id}/community/keys/recovery", _IDENTITY_VIEW),
    ("POST /api/users/{user_id}/community/enrollment/start", _IDENTITY_ENROLL),
    ("POST /api/users/{user_id}/community/enrollment/complete", _IDENTITY_ENROLL),
    ("DELETE /api/users/{user_id}/community/enrollment", _IDENTITY_ENROLL),
    ("PATCH DELETE /api/users/{user_id}/community/profile", _IDENTITY_PROFILE),
    ("POST /api/users/{user_id}/community/keys/rotate", _IDENTITY_KEYS),
    ("POST /api/users/{user_id}/community/keys/rotation-status", _IDENTITY_KEYS),
    ("POST /api/users/{user_id}/community/keys/rotation-retry", _IDENTITY_KEYS),
    ("POST /api/users/{user_id}/community/keys/recovery/start", _IDENTITY_KEYS),
    ("POST /api/users/{user_id}/community/keys/recovery/complete", _IDENTITY_KEYS),
    ("POST /api/users/{user_id}/community/keys/recovery/check", _IDENTITY_KEYS),
    ("POST /api/users/{user_id}/community/keys/recovery/retry", _IDENTITY_KEYS),
    ("POST /api/community/lookup", _gated(C.COMMUNITY_SEARCH)),
    ("POST /api/community/downloads", _gated(C.COMMUNITY_DOWNLOAD)),
    ("POST /api/community/downloads/status", _gated(C.COMMUNITY_DOWNLOAD)),
    ("GET POST /api/community/votes/{revision_id}", _gated(C.COMMUNITY_FEEDBACK_SUBMIT)),
    ("POST /api/community/votes/{revision_id}/retry", _gated(C.COMMUNITY_FEEDBACK_SUBMIT)),
    ("GET /api/community/sharing/sources/{kind}/{source_id}", _SHARE_CONTEXT),
    ("POST /api/community/sharing/sources/{kind}/{source_id}/preview", _SHARE),
    ("POST /api/community/sharing/previews/{preview_id}/approve", _SHARE),
    ("DELETE /api/community/sharing/previews/{preview_id}", _SHARE),
    ("GET /api/community/publications/{operation_id}", _PUBLICATION_CHECK),
    ("POST /api/community/publications/{operation_id}/check", _PUBLICATION_CHECK),
    (
        "POST /api/community/publications/{operation_id}/retry",
        _gated(C.COMMUNITY_PUBLICATIONS_RETRY_OWN, C.COMMUNITY_PUBLICATIONS_RETRY_OTHER),
    ),
    (
        "DELETE /api/community/publications/{operation_id}",
        _gated(C.COMMUNITY_PUBLICATIONS_RETRACT_OWN, C.COMMUNITY_PUBLICATIONS_RETRACT_OTHER),
    ),
    ("POST /api/community/find", _SEARCH),
    ("GET /api/community/aliases", _SEARCH),
    ("POST /api/community/masters", _DOWNLOAD),
    ("GET /api/community/masters", _SEARCH),
    ("GET /api/community/masters/{link_id}", _SEARCH),
    ("GET /api/community/masters/{link_id}/active-filters", _SEARCH),
    ("POST /api/community/masters/{link_id}/alignment/preview", _DOWNLOAD),
    ("POST /api/community/masters/{link_id}/alignment/approve", _DOWNLOAD),
    ("GET /api/community/preferences", _SEARCH),
    ("PATCH /api/community/preferences", _DOWNLOAD),
    ("POST /api/community/masters/{link_id}/filters/{filter_id}/vote", _FEEDBACK),
    ("POST /api/community/masters/{link_id}/filters/{filter_id}/vote/retry", _FEEDBACK),
    ("POST /api/community/masters/{link_id}/reports", _FEEDBACK),
    ("POST /api/community/masters/{link_id}/check", _DOWNLOAD),
    ("GET /api/community/sync", _DOWNLOAD),
    ("POST /api/community/sync/run", _DOWNLOAD),
    ("POST /api/community/sync/notifications/read", _DOWNLOAD),
    ("POST /api/community/import-receipts/recover", _DOWNLOAD),
    ("POST /api/community/import-receipts/{import_id}/retry", _DOWNLOAD),
)


def _build(entries: tuple[tuple[str, RouteRule], ...]) -> Mapping[RouteKey, RouteRule]:
    table: dict[RouteKey, RouteRule] = {}
    for line, rule in entries:
        *methods, path = line.split(" ")
        for method in methods:
            if (method, path) in table:
                raise ValueError(f"Duplicate route rule {method} {path}")
            table[(method, path)] = rule
    return MappingProxyType(table)


ROUTE_RULES: Mapping[RouteKey, RouteRule] = _build(_ENTRIES)
