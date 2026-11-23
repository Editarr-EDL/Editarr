"""Verify saved-review pins against the file currently selected for an operation."""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import MediaVersionRow, ScanMediaVersionRow, ScanRow
from scanner.runtime.media_fingerprints import content_digest
from scanner.runtime.scan_version import ScanVersionChanged, file_generation

from .media_identity_store import get_version, review_version_id

_PROOF_KEY = "review_media_proof"
_NO_SCOPE = object()


@dataclass(frozen=True)
class ReviewMediaProof:
    path: Path
    digest: str
    generation: tuple[int, ...]

    def current_digest(self, path: str | Path) -> str | None:
        """Return the observed digest only for the same current local generation."""
        try:
            resolved = Path(path).resolve(strict=True)
            if self.path == resolved and file_generation(str(resolved)) == self.generation:
                return self.digest
        except (OSError, ScanVersionChanged):
            return None
        return None


def _requires_digest(db: Session, review_id: str, scan_ids: tuple[str, ...]) -> bool:
    from .version_reuse import approved_reuse

    ids = set(
        db.scalars(
            select(ScanMediaVersionRow.version_id).where(ScanMediaVersionRow.scan_id.in_(scan_ids))
        )
    )
    if (version := review_version_id(db, review_id)) is not None:
        ids.add(version)
    if (
        db.scalar(
            select(MediaVersionRow.id)
            .where(MediaVersionRow.id.in_(ids), MediaVersionRow.content_sha256.is_not(None))
            .limit(1)
        )
        is not None
    ):
        return True
    return any(
        (reuse := approved_reuse(scan)) and reuse.get("target_sha256")
        for scan in db.scalars(
            select(ScanRow).where(ScanRow.id.in_(scan_ids), ScanRow.origin == "imported")
        )
    )


def capture_review_media_proof(
    db: Session, review_id: str, path: str, scan_ids: tuple[str, ...]
) -> ReviewMediaProof | None:
    """Read exact media outside the transaction when a known review or scan pin needs it."""
    if not path or not _requires_digest(db, review_id, scan_ids):
        return None
    db.rollback()
    try:
        resolved = Path(path).resolve(strict=True)
        generation = file_generation(str(resolved))
        digest = content_digest(resolved)
        proof = ReviewMediaProof(resolved, digest, generation)
        return proof if proof.current_digest(path) is not None else None
    except (OSError, ValueError):
        return None


@contextmanager
def use_review_media_proof(db: Session, proof: ReviewMediaProof | None):
    """Limit exact file checks in the locked operation to the captured media proof."""
    previous = db.info.get(_PROOF_KEY, _NO_SCOPE)
    db.info[_PROOF_KEY] = proof
    try:
        yield
    finally:
        if previous is _NO_SCOPE:
            db.info.pop(_PROOF_KEY, None)
        else:
            db.info[_PROOF_KEY] = previous


def version_file_matches(
    version: MediaVersionRow | None, path: str | Path, *, db: Session | None = None
) -> bool:
    """Require strong evidence before treating any file as a pinned version."""
    return version is not None and file_digest_matches(path, version.content_sha256, db=db)


def file_digest_matches(
    path: str | Path, expected: str | None, *, db: Session | None = None
) -> bool:
    """Compare a file to an independently captured SHA-256 digest."""
    if expected is None:
        return False
    if db is not None and _PROOF_KEY in db.info:
        proof = db.info[_PROOF_KEY]
        return proof is not None and proof.current_digest(path) == expected
    try:
        return content_digest(path) == expected
    except (OSError, ValueError):
        return False


def review_file_matches(db: Session, review_id: str, path: str | Path) -> bool | None:
    """Report the exact match for a pinned review or absence of a version pin."""
    version_id = review_version_id(db, review_id)
    if version_id is None:
        return None
    return version_file_matches(get_version(db, version_id), path, db=db)
