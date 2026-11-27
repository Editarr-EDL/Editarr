"""Curated OpenAPI tags assigned to operations by ordered path rules."""

import re

TAGS: tuple[tuple[str, str], ...] = (
    ("Authentication", "Setup, Jellyfin sign-in, Quick Connect, session status, and sign-out."),
    ("API keys", "Personal API keys for your account and keys managed for other users."),
    ("Users & permissions", "Accounts, roles, permission overrides, and media access."),
    ("Jellyfin accounts", "Jellyfin users known to Editarr, sync, and sign-in blocks."),
    ("Audit history", "Security and administration events."),
    ("Library", "Jellyfin libraries, items, media versions, audio tracks, and the archive."),
    ("Scans", "Start, queue, follow, and cancel scans."),
    ("Models", "Local AI models, downloads, and scan model settings."),
    ("Reviews", "Saved reviews, cue edits, previews, and retiming."),
    ("Subtitles", "Filtered subtitle sources, previews, and Jellyfin publications."),
    ("Exports & imports", "EDL and MCF exports, publications, and file imports."),
    ("Credits", "Credits detection results."),
    ("Community", "Community connection, master filter sets, sharing, and keys."),
    ("Settings", "Deployment, Jellyfin, filter defaults, path mappings, and macOS settings."),
    ("Backups & restore", "Optionally encrypted backups, schedules, and fresh-install restore."),
    ("System", "Version, capabilities, logs, and restart."),
)

_USER = r"/api/users/\{user_id\}"
_ITEM = r"/api/library-items/\{item_id\}"
_REVIEW = r"/api/reviews/\{review_id\}"

# First matching pattern wins, so specific sub-resources precede their parents.
RULES: tuple[tuple[str, str], ...] = (
    (r"/api/auth/api-key|" + _USER + "/api-key", "API keys"),
    (r"/api/auth/", "Authentication"),
    (_USER + "/community/|/api/community/|/api/filter-candidates", "Community"),
    (r"/api/users|/api/roles/|/api/media-access/", "Users & permissions"),
    (r"/api/jellyfin/", "Jellyfin accounts"),
    (r"/api/audit", "Audit history"),
    (_REVIEW + "/subtitles/|/api/media/subtitles/", "Subtitles"),
    (
        _REVIEW + "/(edl|mcf|exports)|" + _ITEM + "/exports|/api/exports|"
        r"/api/(edl|mcf)-imports/|/api/mcf-registry",
        "Exports & imports",
    ),
    (_REVIEW + "|" + _ITEM + "/reviews", "Reviews"),
    (r"/api/scans|" + _ITEM + "/scans", "Scans"),
    (r"/api/models|/api/settings/models", "Models"),
    (r"/api/credits-detections", "Credits"),
    (
        r"/api/(libraries|library-items|editarr-items|media-versions|media-fingerprints|archive)"
        r"|/api/media/audio-tracks",
        "Library",
    ),
    (r"/api/settings/", "Settings"),
    (r"/api/backups/|/api/setup-restore/", "Backups & restore"),
    (r"/api/system/|/api/capabilities", "System"),
)

_COMPILED = tuple((re.compile(pattern), tag) for pattern, tag in RULES)


def tag_for(path: str) -> str:
    """Return the curated tag for an API path, or raise when no rule claims it."""
    for pattern, tag in _COMPILED:
        if pattern.match(path):
            return tag
    raise LookupError(f"No OpenAPI tag rule matches {path}; add one in openapi_tags.RULES")
