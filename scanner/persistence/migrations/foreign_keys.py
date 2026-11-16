"""Revision-aware admission for historical deleted-scan references."""

import sqlite3

REPAIR_REVISION = "0020_review_scan_repairs"
_PRE_REPAIR = frozenset(
    """
0001_current_baseline 0002_scan_provenance 0003_local_community
0004_media_identity 0005_audio_tracks 0006_community_evidence
0007_community_publications 0008_api_key 0009_readable_api_key
0010_user_accounts 0011_activity_ownership 0012_personal_filter_values
0013_community_identities 0014_media_access_exceptions 0015_sealed_api_keys
0016_personal_filter_rules 0017_user_profiles 0018_jellyfin_account_sync
0019_review_policy_evidence
""".split()
)


def foreign_key_failures(database: sqlite3.Connection) -> str | None:
    """Summarize fatal violations; only recognized pre-0020 files allow scan orphans."""
    versioned = database.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
    ).fetchone()
    revisions = (
        database.execute("SELECT version_num FROM alembic_version").fetchall() if versioned else []
    )
    historical = not versioned or (len(revisions) == 1 and revisions[0][0] in _PRE_REPAIR)
    violations = {}
    for child, _rowid, parent, _constraint in database.execute("PRAGMA foreign_key_check"):
        if historical and (
            parent in {"scans", "scan_findings"}
            or (child, parent) == ("community_import_targets", "community_imports")
        ):
            continue
        violations[child, parent] = violations.get((child, parent), 0) + 1
    return (
        "; ".join(
            f"child={child}, parent={parent}, count={count}"
            for (child, parent), count in sorted(violations.items())
        )
        or None
    )
