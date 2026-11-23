"""Verify saved-review pins against the file currently selected for an operation."""

from pathlib import Path

from sqlalchemy.orm import Session

from scanner.db import MediaVersionRow
from scanner.runtime.media_fingerprints import content_digest

from .media_identity_store import get_version, review_version_id


def version_file_matches(version: MediaVersionRow | None, path: str | Path) -> bool:
    """Require strong evidence before treating any file as a pinned version."""
    return version is not None and file_digest_matches(path, version.content_sha256)


def file_digest_matches(path: str | Path, expected: str | None) -> bool:
    """Compare a file to an independently captured SHA-256 digest."""
    if expected is None:
        return False
    try:
        return content_digest(path) == expected
    except (OSError, ValueError):
        return False


def review_file_matches(db: Session, review_id: str, path: str | Path) -> bool | None:
    """Report the exact match for a pinned review or absence of a version pin."""
    version_id = review_version_id(db, review_id)
    if version_id is None:
        return None
    return version_file_matches(get_version(db, version_id), path)
