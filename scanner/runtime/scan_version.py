"""Keep a worker on the file version captured when its scan was queued."""

from hashlib import sha256

from sqlalchemy.orm import Session

from scanner.persistence.repository.media_identity_store import get_version, scan_version_id

from .media_fingerprints import content_digest


class ScanVersionChanged(ValueError):
    """The scan cannot safely read or publish results from its present file."""


_FAILURE = "The media file changed after this scan was queued. Start a new scan."


def expected_scan_digest(db: Session, scan_id: str) -> str | None:
    """Read the pinned digest, if this scan has verified version evidence."""
    version_id = scan_version_id(db, scan_id)
    if version_id is None:
        return None
    version = get_version(db, version_id)
    if version is None:
        raise ScanVersionChanged(_FAILURE)
    return version.content_sha256


def require_scan_digest(path: str, expected: str | None) -> None:
    """Check exact file bytes for new scans, retaining legacy worker behavior."""
    if expected is None:
        return
    try:
        actual = content_digest(path)
    except (OSError, ValueError) as exc:
        raise ScanVersionChanged(_FAILURE) from exc
    if actual != expected:
        raise ScanVersionChanged(_FAILURE)


_GENERATION = "queued_media_generation"


def file_generation(path: str) -> tuple[int, ...]:
    """Capture the local file generation before a job enters the queue."""
    from pathlib import Path

    try:
        stat = Path(path).stat()
    except OSError as exc:
        raise ScanVersionChanged(_FAILURE) from exc
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_mode


def generation_token(generation: tuple[int, ...]) -> str:
    """Return an opaque identifier for a file observation used in read-only previews."""
    return sha256(":".join(map(str, generation)).encode("ascii")).hexdigest()


def stage_scan_generation(
    db: Session, scan_id: str, path: str, generation: tuple[int, ...]
) -> None:
    """Store the selected generation in the same transaction as the new job."""
    import json

    from scanner.db import ScanRow
    from scanner.persistence.repository.repository_checkpoints import _checkpoint_state

    if file_generation(path) != generation:
        raise ScanVersionChanged(_FAILURE)
    row = db.get(ScanRow, scan_id)
    row.checkpoint_state = json.dumps({**_checkpoint_state(row), _GENERATION: generation})


def _pin_queued_scan(
    db: Session, scan_id: str, path: str, generation: tuple[int, ...], digest: str
):
    import json

    from scanner.db import ScanRow
    from scanner.persistence.repository.media_identity_store import observe_version
    from scanner.persistence.repository.repository_checkpoints import _checkpoint_state

    row = db.get(ScanRow, scan_id)
    state = _checkpoint_state(row)
    if state.get(_GENERATION) != list(generation) or file_generation(path) != generation:
        raise ScanVersionChanged(_FAILURE)
    observe_version(
        db,
        media=row.media_item,
        media_source_id=row.media_source_id,
        source_path=row.source_path or path,
        resolved_path=path,
        content_sha256=digest,
        video_hash=row.video_hash,
        scan_id=scan_id,
    )
    state.pop(_GENERATION)
    row.checkpoint_state = json.dumps(state)
    db.commit()


def prepare_scan_digest(db: Session, scan_id: str, path: str, *, write, on_hashing) -> str | None:
    """Pin deferred evidence before any analysis, or verify an existing pin."""
    from scanner.db import ScanRow
    from scanner.persistence.repository.repository_checkpoints import _checkpoint_state

    known = expected_scan_digest(db, scan_id)
    if known is not None:
        require_scan_digest(path, known)
        return known
    state = _checkpoint_state(db.get(ScanRow, scan_id))
    captured = state.get(_GENERATION)
    if captured is None:
        return None
    generation = file_generation(path)
    if captured != list(generation):
        raise ScanVersionChanged(_FAILURE)
    on_hashing()
    try:
        digest = content_digest(path)
    except (OSError, ValueError) as exc:
        raise ScanVersionChanged(_FAILURE) from exc
    write(_pin_queued_scan, scan_id, path, generation, digest)
    return digest
