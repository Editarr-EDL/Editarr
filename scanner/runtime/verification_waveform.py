"""Verify individual mutes against the two tracks' cached audio."""

import wave
from bisect import bisect_left
from contextlib import closing

import numpy as np

from scanner.modeling.cue_alignment import AudioCue
from scanner.modeling.version_alignment import Alignment
from scanner.pipeline.audio_tracks import AudioTrackSelection
from scanner.pipeline.cache import cached_audio_path

_RATE = 16_000
_SEARCH_SECONDS = 2
_MAX_CUES = 240


def _samples(audio: wave.Wave_read, first: int, last: int) -> np.ndarray:
    audio.setpos(first)
    return np.frombuffer(audio.readframes(last - first), dtype="<i2").astype(np.float64)


def _match(original: np.ndarray, search: np.ndarray) -> int | None:
    """Return the start sample of a strong, unique match in the search window."""
    size = len(original)
    if size < _RATE or len(search) < size:
        return None
    original -= original.mean()
    power = np.dot(original, original)
    if power < size * 32**2:
        return None
    length = 1 << (len(search) + size - 1).bit_length()
    correlation = np.fft.irfft(np.fft.rfft(search, length) * np.fft.rfft(original[::-1], length))[
        size - 1 : len(search)
    ]
    totals = np.append(0, np.cumsum(search))
    energy = np.append(0, np.cumsum(search**2))
    local_power = energy[size:] - energy[:-size] - (totals[size:] - totals[:-size]) ** 2 / size
    scores = correlation / np.sqrt(power * np.maximum(local_power, size * 32**2))
    index = int(np.argmax(scores))
    separate = round(0.05 * _RATE)
    alternative = max(
        scores[: max(0, index - separate)].max(initial=0), scores[index + separate :].max(initial=0)
    )
    if scores[index] >= 0.86 and scores[index] - alternative >= 0.10:
        return index
    return None


def match_cached_audio_cues(
    cues: list[AudioCue],
    alignment: Alignment,
    original: AudioTrackSelection,
    target: AudioTrackSelection,
    original_offset: float,
    target_offset: float,
) -> dict[tuple[float, float], tuple[float, float]]:
    """Search around an established timing landmark and verify each cue's audio directly."""
    anchors = alignment.anchors
    if not anchors or not cues:
        return {}
    paths = [
        cached_audio_path(track.extraction_cache_key(), create=False)
        for track in (original, target)
    ]
    original_times = [anchor.source_seconds for anchor in anchors]
    matches = {}
    try:
        with (
            closing(wave.open(str(paths[0]), "rb")) as source,
            closing(wave.open(str(paths[1]), "rb")) as destination,
        ):
            if any(
                (audio.getframerate(), audio.getsampwidth(), audio.getnchannels()) != (_RATE, 2, 1)
                for audio in (source, destination)
            ):
                return {}
            for cue in cues[:_MAX_CUES]:
                if not 0 < cue.end - cue.start <= 4:
                    continue
                index = bisect_left(original_times, cue.midpoint)
                nearby = anchors[max(0, index - 1) : index + 1]
                landmark = min(nearby, key=lambda point: abs(point.source_seconds - cue.midpoint))
                if abs(landmark.source_seconds - cue.midpoint) > 600:
                    continue
                shift = landmark.target_seconds - landmark.source_seconds
                start = max(0, round((cue.start - original_offset - 1.5) * _RATE))
                end = min(source.getnframes(), round((cue.end - original_offset + 1.5) * _RATE))
                if end <= start:
                    continue
                expected = start + round((original_offset + shift - target_offset) * _RATE)
                left = max(0, expected - _SEARCH_SECONDS * _RATE)
                right = min(
                    destination.getnframes(), expected + (end - start) + _SEARCH_SECONDS * _RATE
                )
                if right - left < end - start:
                    continue
                match = _match(_samples(source, start, end), _samples(destination, left, right))
                if match is None:
                    continue
                shift = (left + match - start) / _RATE + target_offset - original_offset
                projected = round(cue.start + shift, 6), round(cue.end + shift, 6)
                if any(
                    (
                        anchor.source_seconds < cue.start
                        and anchor.target_seconds > projected[0] + 0.1
                    )
                    or (
                        anchor.source_seconds > cue.end
                        and anchor.target_seconds < projected[1] - 0.1
                    )
                    for anchor in nearby
                ):
                    continue
                matches[cue.start, cue.end] = projected
    except (OSError, EOFError, wave.Error):
        pass
    return matches
