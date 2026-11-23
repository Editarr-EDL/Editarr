"""Explicit authentication route inventory and first-run restore matching."""

import re

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
AUTH_STATUS_PATH = "/api/auth/status"
AUTH_SETUP_PATH = "/api/auth/setup"
AUTH_JELLYFIN_LOGIN_PATH = "/api/auth/jellyfin/login"
AUTH_QUICK_CONNECT_PATH = "/api/auth/jellyfin/quick-connect"
AUTH_QUICK_CONNECT_PATHS = frozenset(
    {
        AUTH_QUICK_CONNECT_PATH,
        *(
            f"{AUTH_QUICK_CONNECT_PATH}/{step}"
            for step in ("initiate", "status", "complete", "cancel")
        ),
    }
)
SETUP_RESTORE_PATHS = frozenset(
    {
        "/api/setup-restore/flow",
        "/api/setup-restore/jobs",
        "/api/setup-restore/jobs/{job_id}",
        "/api/setup-restore/jobs/{job_id}/archive",
        "/api/setup-restore/jobs/{job_id}/preflight",
        "/api/setup-restore/jobs/{job_id}/activate",
    }
)
_SETUP_JOB_PATH = re.compile(
    r"/api/setup-restore/jobs/[0-9a-f]{32}(?:/(archive|preflight|activate))?"
)


def _setup_restore_request(path: str, method: str) -> bool:
    if (path, method) in {
        ("/api/setup-restore/flow", "POST"),
        ("/api/setup-restore/jobs", "POST"),
    }:
        return True
    match = _SETUP_JOB_PATH.fullmatch(path)
    if match is None:
        return False
    return (
        method
        in {
            None: {"GET", "DELETE"},
            "archive": {"PUT"},
            "preflight": {"POST"},
            "activate": {"POST"},
        }[match.group(1)]
    )


RETIRED_SIGN_IN_PATHS = frozenset({"/api/auth/login", "/api/auth/register"})
"""Local password sign-in and registration, removed in 0030.

The middleware passes them through like the other sign-in paths, so a
signed-out client still posting to them gets 404, not `authentication_required`.
"""
AUTH_SESSION_PATHS = frozenset({"/api/auth/logout"})
AUTH_PATHS = frozenset(
    {
        AUTH_STATUS_PATH,
        AUTH_SETUP_PATH,
        AUTH_JELLYFIN_LOGIN_PATH,
        *AUTH_QUICK_CONNECT_PATHS,
        *AUTH_SESSION_PATHS,
    }
)
DOCUMENTATION_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})
# These middleware-owned patterns are the entire anonymous frontend surface.  They
# are deliberately separate from FastAPI's endpoint inventory: adding a route does
# not make it public, and adding a static path needs an explicit review here.
PUBLIC_STATIC_ROUTE_PATHS = frozenset(
    {
        "/",
        "/assets/{asset_path:path}",
        "/apple-touch-icon.png",
        "/editarr-app-icon.png",
        "/favicon-16.png",
        "/favicon-32.png",
        "/favicon.ico",
        "/samwise-app-icon.png",
    }
)
PROTECTED_ROUTE_PATHS = frozenset(
    {
        "/api/audit",
        "/api/audit/{event_id}",
        "/api/archive",
        "/api/settings/jellyfin",
        "/api/settings/jellyfin/test",
        "/api/backups/jobs",
        "/api/backups/schedule",
        "/api/backups/jobs/{job_id}",
        "/api/backups/jobs/{job_id}/download",
        "/api/filter-candidates",
        "/api/filter-candidates/associations",
        "/api/settings/filters",
        "/api/settings/filters/apply",
        "/api/settings/filters/default-mode",
        "/api/settings/filters/personal",
        "/api/settings/filters/personal/mode",
        "/api/settings/filters/users/{user_id}",
        "/api/settings/filters/users/{user_id}/mode",
        "/api/settings/filters/users/{user_id}/policy",
        "/api/libraries",
        "/api/libraries/{library_id}/items",
        "/api/editarr-items/{title_id}",
        "/api/editarr-items/legacy/{item_id}",
        "/api/editarr-items/{title_id}/versions",
        "/api/library-items/{item_id}",
        "/api/library-items/{item_id}/children",
        "/api/library-items/{item_id}/seasons",
        "/api/scans",
        "/api/media/audio-tracks",
        "/api/media/audio-tracks/selection",
        "/api/media/subtitles/publications",
        "/api/media-fingerprints/queue",
        "/api/media-versions/{version_id}/reuse/transcript-status",
        "/api/media-versions/{version_id}/reuse/preview",
        "/api/media-versions/{version_id}/reuse/approve",
        "/api/scans/queue",
        "/api/scans/queue/reorder",
        "/api/scans/queue/{queue_id}",
        "/api/scans/queue/{queue_id}/resume",
        "/api/scans/history",
        "/api/scans/{scan_id}",
        "/api/scans/{scan_id}/references",
        "/api/scans/{scan_id}/findings",
        "/api/credits-detections",
        "/api/credits-detections/{scan_id}",
        "/api/credits-detections/{scan_id}/model-consent",
        "/api/credits-detections/{scan_id}/accept",
        "/api/credits-detections/{scan_id}/clip",
        "/api/reviews/{review_id}",
        "/api/reviews/{review_id}/exports",
        "/api/reviews/{review_id}/deletion",
        "/api/exports",
        "/api/reviews/{review_id}/duplicate",
        "/api/reviews/{review_id}/retime",
        "/api/reviews/{review_id}/repair/recover",
        "/api/reviews/{review_id}/repair/community",
        "/api/library-items/{item_id}/reviews/preview",
        "/api/reviews/{review_id}/filters/{filter_id}/mute-preview",
        "/api/reviews/{review_id}/filters/{filter_id}/video-preview",
        "/api/reviews/{review_id}/edl",
        "/api/reviews/{review_id}/mcf",
        "/api/reviews/{review_id}/subtitles/sources",
        "/api/reviews/{review_id}/subtitles/preview",
        "/api/reviews/{review_id}/subtitles/publish",
        "/api/reviews/{review_id}/exports/{format}",
        "/api/reviews/{review_id}/exports/{format}/download",
        "/api/exports/{export_id}",
        "/api/reviews/{review_id}/mcf/preflight",
        "/api/library-items/{item_id}/reviews",
        "/api/edl-imports/upload",
        "/api/mcf-imports/upload",
        "/api/library-items/{item_id}/scans",
        "/api/library-items/{item_id}/exports",
        "/api/library-items/{item_id}/exports/{format}",
        "/api/library-items/{item_id}/image",
        "/api/settings/path-mappings",
        "/api/settings/path-mappings/{mapping_id}",
        "/api/settings/path-mappings/reorder",
        "/api/settings/path-mappings/resolve",
        "/api/settings/deployment",
        "/api/settings/macos/dock",
        "/api/system/restart",
        "/api/system/logs/settings",
        "/api/system/logs/{channel}",
        "/api/system/version",
        "/api/auth/api-key",
        "/api/auth/api-key/reveal",
        "/api/users/{user_id}/api-key",
        "/api/users/{user_id}/api-key/reveal",
        "/api/users",
        "/api/users/bulk",
        "/api/users/{user_id}",
        "/api/users/{user_id}/avatar",
        "/api/users/{user_id}/status",
        "/api/users/{user_id}/sessions",
        "/api/users/{user_id}/role",
        "/api/users/{user_id}/link/proof",
        "/api/users/{user_id}/link",
        "/api/users/{user_id}/deletion",
        "/api/users/bulk-deletion",
        "/api/users/{user_id}/ownership",
        "/api/users/{user_id}/permissions",
        "/api/users/{user_id}/media-access",
        "/api/roles/defaults",
        "/api/roles/{role}/defaults",
        "/api/jellyfin/users",
        "/api/jellyfin/users/import",
        "/api/jellyfin/sync",
        "/api/jellyfin/blocks",
        "/api/jellyfin/blocks/unblock",
        "/api/media-access/default",
        "/api/capabilities",
        "/api/community/connection",
        "/api/community/lookup",
        "/api/community/downloads",
        "/api/community/downloads/status",
        "/api/community/import-receipts/recover",
        "/api/community/import-receipts/{import_id}/retry",
        "/api/community/votes/{revision_id}",
        "/api/community/votes/{revision_id}/retry",
        "/api/community/enrollment/start",
        "/api/community/connection/status",
        "/api/community/enrollment/complete",
        "/api/community/enrollment",
        "/api/community/profile",
        "/api/community/keys/rotate",
        "/api/community/keys/rotation-status",
        "/api/community/keys/rotation-retry",
        "/api/community/keys/recovery",
        "/api/community/keys/recovery/start",
        "/api/community/keys/recovery/complete",
        "/api/community/keys/recovery/check",
        "/api/community/keys/recovery/retry",
        "/api/users/{user_id}/community/connection",
        "/api/users/{user_id}/community/connection/status",
        "/api/users/{user_id}/community/enrollment/start",
        "/api/users/{user_id}/community/enrollment/complete",
        "/api/users/{user_id}/community/enrollment",
        "/api/users/{user_id}/community/profile",
        "/api/users/{user_id}/community/keys/rotate",
        "/api/users/{user_id}/community/keys/rotation-status",
        "/api/users/{user_id}/community/keys/rotation-retry",
        "/api/users/{user_id}/community/keys/recovery",
        "/api/users/{user_id}/community/keys/recovery/start",
        "/api/users/{user_id}/community/keys/recovery/complete",
        "/api/users/{user_id}/community/keys/recovery/check",
        "/api/users/{user_id}/community/keys/recovery/retry",
        "/api/community/publications/{operation_id}",
        "/api/community/publications/{operation_id}/check",
        "/api/community/publications/{operation_id}/retry",
        "/api/community/sharing/sources/{kind}/{source_id}",
        "/api/community/sharing/sources/{kind}/{source_id}/preview",
        "/api/community/sharing/previews/{preview_id}/approve",
        "/api/community/sharing/previews/{preview_id}",
        "/api/mcf-registry",
        "/api/models",
        "/api/settings/models/scans",
        "/api/models/prefetch",
        "/api/models/{artifact_id}",
        "/api/models/{artifact_id}/cancel",
    }
)
