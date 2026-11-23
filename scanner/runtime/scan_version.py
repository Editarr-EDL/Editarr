"""Keep a worker on the file version captured when its scan was queued."""

import logging
import stat
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from time import monotonic

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.persistence.repository.media_identity_store import get_version, scan_version_id

from .file_generation import file_generation as stat_generation
from .file_generation import generation_matches
from .media_fingerprints import content_digest, remember_content_digest

logger = logging.getLogger(__name__)


class ScanVersionChanged(ValueError):
    """The scan cannot safely read or publish results from its present file."""


_FAILURE = "The media file changed after this scan was queued. Start a new scan."
STALE_AUDIO_SELECTION = (
    "The queued audio selection expired. Start a new scan and choose the audio track."
)


def expected_scan_digest(db: Session, scan_id: str) -> str | None:
    """Read the pinned digest, if this scan has verified version evidence."""
    version_id = scan_version_id(db, scan_id)
    if version_id is None:
        return None
    version = get_version(db, version_id)
    if version is None:
        raise ScanVersionChanged(_FAILURE)
    return version.content_sha256


def require_scan_digest(
    path: str, expected: str | None, *, on_progress: Callable[[int, int], None] | None = None
) -> None:
    """Check exact file bytes for new scans, retaining legacy worker behavior."""
    if expected is None:
        return
    try:
        actual = content_digest(path, on_progress=on_progress)
    except (OSError, ValueError) as exc:
        raise ScanVersionChanged(_FAILURE) from exc
    if actual != expected:
        raise ScanVersionChanged(_FAILURE)


_GENERATION = "queued_media_generation"
_VERIFIED_GENERATION = "verified_media_generation"


def file_generation(path: str) -> tuple[int, ...]:
    """Capture the local file generation before a job enters the queue."""
    try:
        metadata = Path(path).stat()
    except OSError as exc:
        raise ScanVersionChanged(_FAILURE) from exc
    return stat_generation(metadata, path)


def generation_token(generation: tuple[int, ...]) -> str:
    """Return an opaque identifier for a file observation used in read-only previews."""
    return sha256(":".join(map(str, generation)).encode("ascii")).hexdigest()


def completed_scan_digest(
    db: Session,
    *,
    server_id: str,
    item_id: str,
    media_source_id: str | None,
    path: Path,
    audio_evidence: tuple[tuple[int, ...], str] | None = None,
) -> str | None:
    """Reuse a worker's full digest only while its exact local file generation holds."""
    from av.error import FFmpegError

    from scanner.db import MediaItemRow, ScanRow
    from scanner.persistence.repository.repository_audio_tracks import recorded_playback_offset
    from scanner.persistence.repository.repository_checkpoints import _checkpoint_state
    from scanner.persistence.rows.db_media_identity import MediaVersionRow, ScanMediaVersionRow
    from scanner.pipeline.audio_tracks import probe_audio_tracks

    from .version_reuse_audio import analyzed_selection

    try:
        path = path.resolve(strict=True)
        generation = file_generation(str(path))
    except (OSError, ScanVersionChanged):
        return None
    if not generation[1] or not stat.S_ISREG(generation[-1]):
        return None

    records = db.execute(
        select(ScanRow, MediaVersionRow.content_sha256)
        .join(MediaItemRow, MediaItemRow.id == ScanRow.media_item_id)
        .join(ScanMediaVersionRow, ScanMediaVersionRow.scan_id == ScanRow.id)
        .join(MediaVersionRow, MediaVersionRow.id == ScanMediaVersionRow.version_id)
        .where(
            MediaItemRow.server_id == server_id,
            MediaItemRow.jellyfin_id == item_id,
            ScanRow.media_source_id == media_source_id,
            ScanRow.resolved_local_path == str(path),
            ScanRow.origin == "generated",
            ScanRow.status == "complete",
            MediaVersionRow.content_sha256.is_not(None),
        )
        .order_by(ScanRow.created_at.desc())
        .limit(32)
    ).all()
    digests = set()
    legacy_tracks = []
    for scan, digest in records:
        stamp = _checkpoint_state(scan).get(_VERIFIED_GENERATION)
        if generation_matches(stamp, generation):
            digests.add(digest)
        elif (
            stamp is None
            and scan.analyzed_audio_selection_json == scan.requested_audio_selection_json
        ):
            # Older workers measured the playback clock and rechecked this file's
            # track inventory after analysis before marking their strong pin complete.
            audio = analyzed_selection(scan)
            if audio and recorded_playback_offset(scan, audio) is not None:
                legacy_tracks.append((audio.file_version, digest))

    if legacy_tracks:
        try:
            audio_version = (
                audio_evidence[1]
                if audio_evidence is not None and audio_evidence[0] == generation
                else probe_audio_tracks(path).file_version
            )
        except (OSError, FFmpegError, ValueError):
            logger.debug("Legacy scan fingerprint evidence could not be confirmed by audio probe")
        else:
            digests.update(digest for version, digest in legacy_tracks if version == audio_version)
    try:
        if len(digests) == 1 and file_generation(str(path)) == generation:
            digest = digests.pop()
            remember_content_digest(path, generation, digest)
            return digest
    except ScanVersionChanged:
        pass
    return None


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
    if (
        not generation_matches(state.get(_GENERATION), generation)
        or file_generation(path) != generation
    ):
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
    state[_VERIFIED_GENERATION] = list(generation)
    row.checkpoint_state = json.dumps(state)
    db.commit()


def scan_digest_progress(write: Callable[[dict[str, object]], None]) -> Callable[[int, int], None]:
    """Convert actual fingerprint bytes to at most one queue update per second."""
    next_update = None

    def report(done: int, total: int) -> None:
        nonlocal next_update
        now = monotonic()
        if not total or (next_update is not None and now < next_update):
            return
        write({"stage": "hashing", "message": "Identifying media", "percent": 100 * done / total})
        next_update = now + 1.0

    return report


def prepare_scan_digest(
    db: Session,
    scan_id: str,
    path: str,
    *,
    write,
    on_progress: Callable[[int, int], None] | None = None,
) -> str | None:
    """Pin deferred evidence before any analysis, or verify an existing pin."""
    from scanner.db import ScanRow
    from scanner.persistence.repository.repository_checkpoints import _checkpoint_state

    known = expected_scan_digest(db, scan_id)
    if known is not None:
        require_scan_digest(path, known, on_progress=on_progress)
        return known
    state = _checkpoint_state(db.get(ScanRow, scan_id))
    captured = state.get(_GENERATION)
    if captured is None:
        return None
    generation = file_generation(path)
    if not generation_matches(captured, generation):
        raise ScanVersionChanged(_FAILURE)
    try:
        digest = content_digest(path, on_progress=on_progress)
    except (OSError, ValueError) as exc:
        raise ScanVersionChanged(_FAILURE) from exc
    write(_pin_queued_scan, scan_id, path, generation, digest)
    return digest
