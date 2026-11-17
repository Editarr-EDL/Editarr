"""Translate extracted audio timestamps to the media playback timeline."""

from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from .audio_tracks import PROBE_OPTIONS, AudioSelectionError, AudioTrackSelection
from .bounded_media import BoundedInput
from .processes import ScanCancelledError


def audio_playback_offset(
    video_path: str,
    audio_selection: AudioTrackSelection,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> float:
    """Map WAV sample zero to playback time using the selected stream's decoded clock."""
    return float(audio_playback_start(video_path, audio_selection, should_cancel=should_cancel))


def audio_playback_start(
    video_path: str | Path,
    audio_selection: AudioTrackSelection,
    *,
    should_cancel: Callable[[], bool] | None = None,
    bounded: bool = False,
) -> Fraction:
    """Return the selected stream's first decoded sample time relative to media playback."""
    import av

    def check_cancel():
        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")
        return False

    # Normal scans can decode delayed tracks beyond the community probe budget.
    limits = {} if bounded else {"max_bytes": None, "max_seconds": None, "max_seeks": None}
    with BoundedInput(
        Path(video_path).resolve(strict=True),
        check_cancel,
        **limits,
    ) as source:
        with av.open(source, options=dict(PROBE_OPTIONS), timeout=5.0) as container:
            stream = next(
                (
                    candidate
                    for candidate in container.streams.audio
                    if candidate.index == audio_selection.track.stream_index
                ),
                None,
            )
            if stream is None:
                raise AudioSelectionError("The selected audio stream is unavailable")
            frame = next(container.decode(stream), None)
            if frame is None or frame.pts is None or frame.time_base is None:
                raise AudioSelectionError("The selected audio stream has no decoded start time")
            if container.start_time is None:
                raise AudioSelectionError("The media container has no playback start time")
            source.check()
            return frame.pts * frame.time_base - Fraction(container.start_time, av.time_base)
