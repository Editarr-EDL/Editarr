"""Fingerprint the scanner's normalized samples and their playback timeline."""

import hashlib
import subprocess
import time
import wave
from collections import OrderedDict
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Literal

import av

from scanner.formats.community.stream_fingerprints import PCM_AUDIO_ALGORITHM
from scanner.modeling.community_stream_matching import StreamEvidence
from scanner.pipeline.audio_timing import audio_playback_start
from scanner.pipeline.audio_tracks import (
    AUDIO_PCM_OPTIONS,
    AUDIO_PCM_RATE,
    AudioTrackSelection,
    _file_state,
    probe_audio_tracks,
)
from scanner.pipeline.cache import cached_audio_path
from scanner.pipeline.processes import ScanCancelledError, run_command
from scanner.runtime.runtime_support import resolve_ffmpeg

from .stream_fingerprints import StreamUnsupported

_MAX_SAMPLES = 4 * 60 * 60 * AUDIO_PCM_RATE
_MAX_DECODE_SECONDS = 180
_SAMPLE_WIDTH = 2
_cache: OrderedDict[tuple, tuple[str, str | None, str]] = OrderedDict()
_lock = Lock()


def _hash_wav(path: Path, check: Callable[[], None]) -> tuple[str, str]:
    """Hash only complete signed 16-bit mono samples, excluding WAV metadata."""
    digest = hashlib.sha256(PCM_AUDIO_ALGORITHM.encode("ascii") + b"\0")
    before = _file_state(path)
    with wave.open(str(path), "rb") as audio:
        count = audio.getnframes()
        if (
            audio.getframerate() != AUDIO_PCM_RATE
            or audio.getnchannels() != 1
            or audio.getsampwidth() != _SAMPLE_WIDTH
            or count <= 0
        ):
            raise wave.Error("Invalid scanner PCM")
        if count > _MAX_SAMPLES:
            raise StreamUnsupported("audio_pcm_budget")
        remaining = count
        while remaining:
            check()
            frames = min(remaining, 32_768)
            samples = audio.readframes(frames)
            if len(samples) != frames * _SAMPLE_WIDTH:
                raise wave.Error("Incomplete scanner PCM")
            digest.update(samples)
            remaining -= frames
    if _file_state(path) != before:
        raise StreamUnsupported("file_changed")
    return digest.hexdigest(), str(Fraction(count, AUDIO_PCM_RATE))


def _decode_pcm(
    path: Path, index: int, check: Callable[[], None], should_cancel: Callable[[], bool] | None
) -> tuple[str, str]:
    deadline = time.monotonic() + _MAX_DECODE_SECONDS
    with TemporaryDirectory(prefix="editarr-pcm-") as scratch:
        wav = Path(scratch) / "audio.wav"
        try:
            run_command(
                [
                    resolve_ffmpeg(),
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(path),
                    "-map",
                    f"0:{index}",
                    "-vn",
                    *AUDIO_PCM_OPTIONS,
                    # Leave enough room for headers so size-limited output exceeds
                    # the sample limit and cannot be accepted as a complete track.
                    "-fs",
                    str(_MAX_SAMPLES * _SAMPLE_WIDTH + 32_768),
                    str(wav),
                ],
                should_cancel=lambda: (
                    (should_cancel is not None and should_cancel()) or time.monotonic() >= deadline
                ),
            )
            return _hash_wav(wav, check)
        except ScanCancelledError as error:
            check()
            raise StreamUnsupported("audio_pcm_budget") from error
        except (
            OSError,
            RuntimeError,
            subprocess.CalledProcessError,
            EOFError,
            wave.Error,
        ) as error:
            raise StreamUnsupported("audio_pcm_unavailable") from error


def fingerprint_pcm_audio(
    video_path: str | Path,
    selection: AudioTrackSelection,
    *,
    provenance: Literal["selected", "analyzed"],
    description: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> StreamEvidence:
    """Return the selected track's PCM digest, exact start, and sample-count duration."""
    path = Path(video_path).resolve(strict=True)
    before = _file_state(path)
    key = (path, before, selection)

    def check():
        if should_cancel and should_cancel():
            raise StreamUnsupported("cancelled")

    check()
    with _lock:
        cached = _cache.get(key) if before[1] else None
        if cached is not None:
            _cache.move_to_end(key)
    try:
        if cached is None:
            probe_audio_tracks(path).revalidate(selection)
            try:
                wav = cached_audio_path(selection.extraction_cache_key())
                digest, duration = _hash_wav(wav, check)
            except (OSError, EOFError, wave.Error):
                digest, duration = _decode_pcm(
                    path, selection.track.stream_index, check, should_cancel
                )
            try:
                start = str(
                    audio_playback_start(path, selection, bounded=True, should_cancel=should_cancel)
                )
            except ScanCancelledError as error:
                raise StreamUnsupported("cancelled") from error
            except (av.FFmpegError, OSError, ValueError):
                start = None
            cached = (digest, start, duration)
    finally:
        check()
        if _file_state(path) != before:
            raise StreamUnsupported("file_changed")
    if before[1] and cached[1] is not None:
        with _lock:
            _cache[key] = cached
            while len(_cache) > 128:
                _cache.popitem(last=False)
    digest, start, duration = cached
    return StreamEvidence(PCM_AUDIO_ALGORITHM, digest, start, duration, provenance, description)
