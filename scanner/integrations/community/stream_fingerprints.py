"""Bounded independent stream evidence, with actual-track-only AI audio access."""

from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock

from sqlalchemy.orm import Session

from scanner.db import ScanRow
from scanner.formats.community.stream_fingerprints import (
    AUDIO_ALGORITHM,
    VIDEO_ALGORITHM,
    stream_digest,
)
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.pipeline.audio_tracks import _file_state

from .stream_experiments import SamplingCandidate, StreamUnsupported, sample_stream


@dataclass(frozen=True)
class StreamFingerprint:
    algorithm: str
    digest: str
    codec: str
    channel: str
    start_seconds: float
    duration_seconds: float
    time_base: str
    bytes_read: int
    payload_bytes: int
    packets_seen: int
    seeks: int
    cache_hit: bool = False
    playback_start: str | None = None
    duration: str | None = None


_cache: OrderedDict[tuple, StreamFingerprint] = OrderedDict()
_lock = Lock()


def fingerprint_stream(
    video_path: str | Path, stream_index: int | None, *, should_cancel=None, audio_selection=None
) -> StreamFingerprint:
    """Return sampled evidence without an unbounded full-stream fallback."""
    path = Path(video_path).resolve(strict=True)
    if should_cancel and should_cancel():
        raise StreamUnsupported("cancelled")
    before = _file_state(path)
    key = (
        str(path),
        before,
        stream_index,
        "three-250ms-config-packets-1-playback-rational-1",
        audio_selection,
    )
    with _lock:
        cached = _cache.get(key) if before[1] else None
        if cached is not None:
            if _file_state(path) != before:
                _cache.pop(key, None)
                raise StreamUnsupported("file_changed")
            _cache.move_to_end(key)
            return replace(
                cached, bytes_read=0, payload_bytes=0, packets_seen=0, seeks=0, cache_hit=True
            )
    result = sample_stream(
        path,
        stream_index,
        SamplingCandidate(3, 0.25),
        should_cancel=should_cancel,
        audio_selection=audio_selection,
    )
    if _file_state(path) != before:
        raise StreamUnsupported("file_changed")
    fingerprint = StreamFingerprint(
        algorithm=AUDIO_ALGORITHM if result["channel"] == "audio" else VIDEO_ALGORITHM,
        digest=stream_digest(
            result["channel"], result["codec"], result["configuration"], result["samples"]
        ),
        codec=result["codec"],
        channel=result["channel"],
        **result["timing"],
        bytes_read=result["bytes_read"],
        payload_bytes=result["payload_bytes"],
        packets_seen=result["packets_seen"],
        seeks=result["seeks"],
    )
    if before[1]:
        with _lock:
            _cache[key] = fingerprint
            while len(_cache) > 128:
                _cache.popitem(last=False)
    return fingerprint


def fingerprint_analyzed_audio(
    db: Session, scan_id: str, *, should_cancel=None
) -> StreamFingerprint:
    """Fingerprint only a generated scan's immutable actually analyzed track."""
    row = db.get(ScanRow, scan_id)
    if (
        row is None
        or row.origin != "generated"
        or not row.scan_audio
        or not row.analyzed_audio_selection_json
    ):
        raise StreamUnsupported("analyzed_audio_unknown")
    selection = AudioSelection.model_validate_json(row.analyzed_audio_selection_json).selection()
    result = fingerprint_stream(
        row.resolved_local_path,
        selection.track.stream_index,
        should_cancel=should_cancel,
        audio_selection=selection,
    )
    if result.channel != "audio":
        raise StreamUnsupported("analyzed_audio_stream_missing")
    return result


def fingerprint_video(video_path: str | Path, *, should_cancel=None) -> StreamFingerprint:
    return fingerprint_stream(video_path, None, should_cancel=should_cancel)
