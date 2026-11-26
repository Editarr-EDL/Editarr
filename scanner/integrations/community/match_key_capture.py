"""Tier 1–3 match keys (§3.2) captured from one stable local media file."""

from collections.abc import Callable
from pathlib import Path

from scanner.formats.community.fingerprints import SAMPLED_ALGORITHM
from scanner.modeling.community_match_keys import FileKey, HashKey, StreamKey, md5_quick_key
from scanner.pipeline.audio_tracks import AudioSelectionError, _file_state

from .fingerprints import FileChangedError, FingerprintCache
from .pcm_fingerprints import fingerprint_pcm_audio
from .stream_fingerprints import StreamUnsupported, fingerprint_video
from .transport import CommunityFailure

_files = FingerprintCache()
# Stream failures that only mean "no stream key", reported as a warning.
UNSUPPORTED_STREAMS = frozenset(
    {
        "unsupported_codec_configuration",
        "missing_stream_timing_index",
        "stream_too_short",
        "empty_sample",
        "h264_requires_avcc",
        "invalid_avcc",
        "invalid_nal_packet",
        "missing_stream",
        "audio_pcm_budget",
        "audio_pcm_unavailable",
    }
)


def _stream(channel: str, measure: Callable, warnings: list[str]) -> StreamKey | None:
    try:
        result = measure()
    except StreamUnsupported as error:
        if str(error) == "cancelled":
            raise CommunityFailure("lookup_cancelled") from error
        if str(error) == "file_changed":
            raise CommunityFailure("file_changed") from error
        if str(error) not in UNSUPPORTED_STREAMS:
            raise CommunityFailure("stream_evidence_unavailable") from error
        warnings.append(f"{channel}_fingerprint_unsupported")
        return None
    except AudioSelectionError as error:
        raise CommunityFailure("audio_selection_stale") from error
    key_type = "audio_stream" if channel == "audio" else "video_stream"
    try:
        if getattr(result, "channel", channel) != channel:
            raise ValueError("Unexpected stream channel")
        return StreamKey(key_type, result.algorithm, result.digest)
    except ValueError:
        warnings.append(f"{channel}_fingerprint_unsupported")
        return None


def capture_file_keys(
    path: Path,
    *,
    audio_selection=None,
    video: bool = True,
    should_cancel: Callable[[], bool] | None = None,
    warnings: list[str],
) -> tuple[FileKey, ...]:
    """Measure `sha256_sampled`, `md5_quick` and, when available, stream keys.

    The file must not change while it is read (`file_changed`). An audio key needs
    the analyzed or selected track; unsupported streams only add a warning.
    """
    before = _file_state(path)

    def check():
        if should_cancel and should_cancel():
            raise CommunityFailure("lookup_cancelled")
        try:
            if _file_state(path) != before:
                raise CommunityFailure("file_changed")
        except (OSError, AudioSelectionError) as error:
            raise CommunityFailure("file_changed") from error

    try:
        sampled = _files.fingerprint(path)
        quick = md5_quick_key(path)
    except FileChangedError as error:
        raise CommunityFailure("file_changed") from error
    if sampled.algorithm != SAMPLED_ALGORITHM or sampled.size_bytes != quick.size_bytes:
        raise CommunityFailure("file_changed")
    keys: list[FileKey] = [
        HashKey("sha256_sampled", sampled.digest, sampled.size_bytes),
        quick,
    ]
    check()
    if video:
        key = _stream(
            "video", lambda: fingerprint_video(path, should_cancel=should_cancel), warnings
        )
        if key is not None:
            keys.append(key)
        check()
    if audio_selection is not None:
        key = _stream(
            "audio",
            lambda: fingerprint_pcm_audio(
                path, audio_selection, provenance="analyzed", should_cancel=should_cancel
            ),
            warnings,
        )
        if key is not None:
            keys.append(key)
        check()
    return tuple(keys)
