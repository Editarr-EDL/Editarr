"""File-bound audio inventory and explicit local stream selection."""

from __future__ import annotations

import hashlib
import json
import math
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from .bounded_media import BoundedInput

MAX_STREAMS = 128
MAX_TAG_LENGTH = 512
PROBE_OPTIONS = {"probesize": "4194304", "analyzeduration": "2000000"}


class AudioSelectionError(ValueError):
    """The requested audio input cannot be selected without confirmation."""


@dataclass(frozen=True)
class AudioTrack:
    stream_index: int
    language: str | None
    title: str | None
    codec: str | None
    channels: int | None
    disposition: int
    start_seconds: float | None
    duration_seconds: float | None
    time_base: str | None

    @property
    def is_default(self) -> bool:
        return bool(self.disposition & 1)


@dataclass(frozen=True)
class AudioTrackSelection:
    file_version: str
    track: AudioTrack

    def extraction_cache_key(self) -> str:
        """Scope WAV and downstream transcript caches to this extraction behavior."""
        payload = json.dumps(
            {"selection": asdict(self), "extraction": "pcm-s16le-16000-mono-1"},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class AudioTrackInventory:
    file_version: str
    tracks: tuple[AudioTrack, ...]

    @property
    def preselected_stream_index(self) -> int | None:
        if len(self.tracks) == 1:
            return self.tracks[0].stream_index
        defaults = [track for track in self.tracks if track.is_default]
        return defaults[0].stream_index if len(defaults) == 1 else None

    def select(self, stream_index: int | None = None) -> AudioTrackSelection:
        """Select an exact physical stream, applying only the approved default rule."""
        if not self.tracks:
            raise AudioSelectionError("No audio tracks are available")
        selected_index = self.preselected_stream_index if stream_index is None else stream_index
        if selected_index is None:
            raise AudioSelectionError("Choose an audio track")
        if isinstance(selected_index, bool) or not isinstance(selected_index, int):
            raise AudioSelectionError("Invalid audio stream index")
        for track in self.tracks:
            if track.stream_index == selected_index:
                return AudioTrackSelection(self.file_version, track)
        raise AudioSelectionError("The selected audio track is unavailable")

    def revalidate(self, selection: AudioTrackSelection) -> AudioTrack:
        """Reject changed file versions or stream metadata without substituting tracks."""
        if selection.file_version != self.file_version:
            raise AudioSelectionError("The file changed; choose an audio track again")
        actual = self.select(selection.track.stream_index).track
        if actual != selection.track:
            raise AudioSelectionError("The audio track changed; choose it again")
        return actual


def _file_state(path: Path) -> tuple[int, int, int, int, int]:
    observed = path.stat()
    if not stat.S_ISREG(observed.st_mode):
        raise AudioSelectionError("Audio input must be a regular file")
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _tag(metadata: dict[str, str], name: str) -> str | None:
    value = metadata.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_TAG_LENGTH:
        raise AudioSelectionError("Audio metadata exceeds supported limits")
    return value.strip() or None


def _seconds(value: int | None, time_base: object) -> float | None:
    if value is None or time_base is None:
        return None
    seconds = float(value * time_base)
    return seconds if math.isfinite(seconds) else None


def inventory_from_streams(path: Path, before: tuple, streams) -> AudioTrackInventory:
    """Build the file-bound inventory from an already bounded media probe."""
    tracks: list[AudioTrack] = []
    if len(streams) > MAX_STREAMS:
        raise AudioSelectionError("Too many media streams")
    for stream in streams.audio:
        context = stream.codec_context
        time_base = stream.time_base
        duration = _seconds(stream.duration, time_base)
        tracks.append(
            AudioTrack(
                stream_index=stream.index,
                language=_tag(stream.metadata, "language"),
                title=_tag(stream.metadata, "title"),
                codec=context.name or None,
                channels=context.channels or None,
                disposition=int(stream.disposition),
                start_seconds=_seconds(stream.start_time, time_base),
                duration_seconds=duration if duration is None or duration >= 0 else None,
                time_base=str(time_base) if time_base is not None else None,
            )
        )
    if _file_state(path) != before:
        raise AudioSelectionError("The file changed while probing audio tracks")
    indexes = [track.stream_index for track in tracks]
    if len(set(indexes)) != len(indexes):
        raise AudioSelectionError("Duplicate audio stream indexes")
    version_bytes = json.dumps(
        {
            "version": 1,
            "path": str(path),
            "state": before,
            "tracks": [asdict(track) for track in tracks],
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return AudioTrackInventory(hashlib.sha256(version_bytes).hexdigest(), tuple(tracks))


def probe_audio_tracks(video_path: str | Path) -> AudioTrackInventory:
    """Probe without decoding, with counted read/seek/time and mutation limits."""
    import av

    path = Path(video_path).resolve(strict=True)
    before = _file_state(path)
    with BoundedInput(path) as source:
        with av.open(source, options=dict(PROBE_OPTIONS), timeout=5.0) as container:
            inventory = inventory_from_streams(path, before, container.streams)
        source.check()
    if _file_state(path) != before:
        raise AudioSelectionError("The file changed while probing audio tracks")
    return inventory
