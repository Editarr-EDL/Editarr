"""Read-only target resolution and private, mutation-checked lookup evidence."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from sqlalchemy.orm import Session

from scanner.integrations.jellyfin import JellyfinClient, JellyfinError, JellyfinNotFound
from scanner.modeling.community_matching import POLICY_VERSION, FingerprintEvidence, MatchEvidence
from scanner.modeling.community_stream_matching import StreamEvidence
from scanner.modeling.media_identity import AudioEvidence, DurationEvidence, MediaIdentity
from scanner.modeling.model_community_lookup import LookupRequest, LookupTarget
from scanner.pipeline.audio_tracks import (
    PROBE_OPTIONS,
    AudioSelectionError,
    _file_state,
    probe_audio_tracks,
)
from scanner.pipeline.bounded_media import BoundedInput
from scanner.pipeline.frames import _positive_stream_duration
from scanner.repository import get_jellyfin_credentials, resolve_local_path

from .donation_projection import _PRIVATE_TEXT
from .fingerprints import FileChangedError, FingerprintCache
from .stream_fingerprints import StreamUnsupported, fingerprint_stream, fingerprint_video
from .transport import CommunityFailure

_files = FingerprintCache()
_unsupported = {
    "unsupported_codec_configuration",
    "missing_stream_timing_index",
    "stream_too_short",
    "empty_sample",
    "h264_requires_avcc",
    "invalid_avcc",
    "invalid_nal_packet",
    "missing_stream",
}


class MissingTarget(CommunityFailure):
    """A well-formed target has no local row or is confirmed absent by its provider."""

    def __init__(self):
        super().__init__("invalid_target")


def _measured_duration(path: Path, should_cancel=None) -> float:
    """Read duration metadata inside the existing probe budget; never count frames."""
    import av

    try:
        with BoundedInput(path, should_cancel) as source:
            with av.open(source, options=dict(PROBE_OPTIONS), timeout=5.0) as container:
                duration = _positive_stream_duration(container.streams.video)
                if duration is None and container.duration is not None:
                    duration = float(container.duration / av.time_base)
            source.check()
    except StreamUnsupported as error:
        if str(error) == "cancelled":
            raise CommunityFailure("lookup_cancelled") from error
        raise
    if duration is None:
        raise ValueError("Duration metadata unavailable")
    return duration


@dataclass(frozen=True)
class CapturedTarget:
    path: Path
    state: tuple
    evidence: MatchEvidence
    key: str
    warnings: tuple[str, ...]

    def check(self, should_cancel=None):
        if should_cancel and should_cancel():
            raise CommunityFailure("lookup_cancelled")
        try:
            if _file_state(self.path) != self.state:
                raise CommunityFailure("file_changed")
        except (OSError, AudioSelectionError) as error:
            raise CommunityFailure("file_changed") from error


def resolve_target(
    db: Session, target: LookupTarget, *, authorize=None
) -> tuple[Path, MediaIdentity]:
    """Resolve one provider version without creating/updating media or review rows."""
    conflict = None
    credentials = get_jellyfin_credentials(db)
    if not credentials:
        raise CommunityFailure("target_unavailable", retryable=True)
    try:
        with JellyfinClient(*credentials) as client:
            item = client.item(target.item_id)
    except JellyfinNotFound as error:
        raise MissingTarget() from error
    except JellyfinError as error:
        raise CommunityFailure("target_unavailable", retryable=True) from error
    version = next(
        (value for value in item.versions if value.id == target.media_source_id), None
    )
    if target.media_source_id and version is None:
        conflict = "target_changed"
    elif version is None and len(item.versions) > 1:
        conflict = "version_required"
    version = version or (item.versions[0] if item.versions else None)
    source_path = version.path if version else item.path
    media = (version.media_identity if version else None) or item.media_identity
    try:
        path = (
            Path(resolve_local_path(db, source_path).local_path).resolve() if source_path else None
        )
        if authorize is not None:
            from scanner.persistence.repository.scan_jobs import ScanMedia

            resolved = str(path) if path else ""
            authorize(ScanMedia(credentials[0], target.item_id, item.library_id, resolved))
        if conflict is not None:
            raise CommunityFailure(conflict)
        if media is None:
            raise CommunityFailure("unsupported_target")
        if path is None:
            raise CommunityFailure("target_unavailable")
        path = path.resolve(strict=True)
        _file_state(path)
        return path, media
    except (OSError, ValueError) as error:
        raise CommunityFailure("target_unavailable") from error


def capture_target(
    db: Session, request: LookupRequest, *, should_cancel=None, authorize=None
) -> CapturedTarget:
    """Capture only portable evidence; selectors, inventories and paths remain private."""
    path, media = (
        resolve_target(db, request.target, authorize=authorize)
        if authorize is not None
        else resolve_target(db, request.target)
    )
    before = _file_state(path)
    warnings = []

    def check():
        if should_cancel and should_cancel():
            raise CommunityFailure("lookup_cancelled")
        try:
            if _file_state(path) != before:
                raise CommunityFailure("file_changed")
        except (OSError, AudioSelectionError) as error:
            raise CommunityFailure("file_changed") from error

    check()
    try:
        file = _files.fingerprint(path)
    except FileChangedError as error:
        raise CommunityFailure("file_changed") from error
    audio = None
    audio_stream = None
    selection = request.audio_selection.selection() if request.audio_selection else None
    if selection:
        try:
            probe_audio_tracks(path).revalidate(selection)
        except (OSError, ValueError) as error:
            raise CommunityFailure("audio_selection_stale") from error
        track = selection.track
        language = (
            track.language
            if track.language and re.fullmatch(r"[A-Za-z0-9-]{1,64}", track.language)
            else None
        )
        role = (
            "commentary"
            if track.disposition & 8
            else "descriptive"
            if track.disposition & 256
            else "unknown"
        )
        audio = AudioEvidence(language, role, track.codec, track.channels)
    else:
        warnings.append("audio_selection_required")

    def stream(channel):
        check()
        try:
            result = (
                fingerprint_stream(
                    path,
                    selection.track.stream_index,
                    should_cancel=should_cancel,
                    audio_selection=selection,
                )
                if channel == "audio"
                else fingerprint_video(path, should_cancel=should_cancel)
            )
            if result.channel != channel:
                raise CommunityFailure("unsupported_stream")
            return StreamEvidence(
                result.algorithm,
                result.digest,
                result.playback_start,
                result.duration,
                "selected" if channel == "audio" else "unknown",
                selection.track.title
                if channel == "audio"
                and selection.track.title
                and not _PRIVATE_TEXT.search(selection.track.title)
                else None,
            )
        except StreamUnsupported as error:
            if str(error) in {"cancelled", "file_changed"}:
                raise CommunityFailure(
                    "lookup_cancelled" if str(error) == "cancelled" else "file_changed"
                ) from error
            if str(error) not in _unsupported:
                raise CommunityFailure("stream_evidence_unavailable") from error
            warnings.append(f"{channel}_fingerprint_unsupported")
            return None

    video = stream("video")
    if selection:
        audio_stream = stream("audio")
    try:
        duration = DurationEvidence(_measured_duration(path, should_cancel), "file_probe")
    except (OSError, RuntimeError, ValueError):
        duration = None
        warnings.append("measured_duration_unavailable")
    media = replace(
        media,
        release=replace(media.release, size_bytes=file.size_bytes, duration=duration, audio=audio),
    )
    evidence = MatchEvidence(
        media,
        (FingerprintEvidence(file.algorithm, file.digest, file.size_bytes),),
        video,
        audio_stream,
    )
    check()
    key = hashlib.sha256(
        json.dumps(
            [
                POLICY_VERSION,
                str(path),
                before,
                asdict(selection) if selection else None,
                asdict(evidence),
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if request.cursor and (request.target_key != key or request.refresh):
        raise CommunityFailure("target_changed")
    return CapturedTarget(path, before, evidence, key, tuple(warnings))
