"""New table inventory for comparisons with historical migration snapshots."""

STABLE_MEDIA_IDENTITY_TABLES = (
    "media_titles",
    "media_title_sources",
    "media_title_aliases",
    "media_versions",
    "media_version_locators",
    "scan_media_versions",
    "review_media_versions",
)


def without_stable_media_identity(snapshot):
    """Project a head snapshot onto the tables pre-0035 sources can contain."""
    return {
        name: rows for name, rows in snapshot.items() if name not in STABLE_MEDIA_IDENTITY_TABLES
    }
