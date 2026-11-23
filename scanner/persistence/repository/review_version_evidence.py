"""Verify saved-review pins against the file currently selected for an operation."""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, object_session

from scanner.db import MediaVersionRow, ScanMediaVersionRow, ScanRow
from scanner.runtime.media_fingerprints import (
    SampledMediaFingerprint,
    cached_content_digest,
    sampled_media_fingerprint,
)
from scanner.runtime.scan_version import ScanVersionChanged, file_generation

from .media_identity_store import get_version, matches_sampled_version, review_version_id

_PROOF_KEY = "review_media_proof"
_NO_SCOPE = object()


@dataclass(frozen=True)
class ReviewMediaProof:
    fingerprint: SampledMediaFingerprint
    known_content_sha256: str | None = None

    def current_fingerprint(self, path: str | Path) -> SampledMediaFingerprint | None:
        """Return the bounded proof only for the same current local generation."""
        try:
            resolved = Path(path).resolve(strict=True)
            if (
                self.fingerprint.path == resolved
                and file_generation(str(resolved)) == self.fingerprint.generation
            ):
                return self.fingerprint
        except (OSError, RuntimeError, ScanVersionChanged):
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
            .where(
                MediaVersionRow.id.in_(ids),
                or_(
                    MediaVersionRow.sampled_md5.is_not(None),
                    MediaVersionRow.content_sha256.is_not(None),
                ),
            )
            .limit(1)
        )
        is not None
    ):
        return True
    return any(
        (reuse := approved_reuse(scan))
        and (reuse.get("target_sampled_md5") or reuse.get("target_sha256"))
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
    return capture_media_proof(path)


def capture_media_proof(path: str | Path) -> ReviewMediaProof | None:
    """Capture a bounded sample and any surviving whole-file witness before a write lock."""
    try:
        fingerprint = sampled_media_fingerprint(path)
        proof = ReviewMediaProof(fingerprint, cached_content_digest(fingerprint.path))
        return proof if proof.current_fingerprint(path) is not None else None
    except (OSError, RuntimeError, ValueError):
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
    """Match a pinned version with a sample or an unchanged surviving SHA witness."""
    if version is None:
        return False
    db = db if db is not None else object_session(version)
    if db is None:
        return False
    proof = db.info[_PROOF_KEY] if _PROOF_KEY in db.info else capture_media_proof(path)
    fingerprint = proof.current_fingerprint(path) if proof is not None else None
    return fingerprint is not None and matches_sampled_version(
        db, version, fingerprint, known_content_sha256=proof.known_content_sha256
    )


def file_digest_matches(
    path: str | Path, expected: str | None, *, db: Session | None = None
) -> bool:
    """Compare a legacy full-file pin only to a surviving unchanged witness."""
    if expected is None:
        return False
    if db is not None and _PROOF_KEY in db.info:
        proof = db.info[_PROOF_KEY]
        return (
            proof is not None
            and proof.current_fingerprint(path) is not None
            and proof.known_content_sha256 == expected
        )
    return cached_content_digest(path) == expected


def review_file_matches(db: Session, review_id: str, path: str | Path) -> bool | None:
    """Report the exact match for a pinned review or absence of a version pin."""
    version_id = review_version_id(db, review_id)
    if version_id is None:
        return None
    return version_file_matches(get_version(db, version_id), path, db=db)
