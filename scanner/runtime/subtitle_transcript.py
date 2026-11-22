"""Project a completed, file-bound audio scan's cache as SubRip captions."""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

from scanner.formats.subtitles import SubtitleDocument, parse_subtitles, srt_time
from scanner.integrations.jellyfin_subtitles import normalize_language
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.modeling.model_subtitles import ReviewAudioSource
from scanner.pipeline.audio import transcription_cache_model_key
from scanner.pipeline.audio_timing import audio_playback_start
from scanner.pipeline.audio_tracks import AudioTrackSelection, probe_audio_tracks
from scanner.pipeline.cache import transcription_cache_path

from .subtitle_sidecars import read_local_bytes

_MAX_TRANSCRIPT_BYTES = 24 * 1024 * 1024


@dataclass(frozen=True)
class ReviewedAudio:
    source: ReviewAudioSource
    selection: AudioTrackSelection
    models: tuple[str | None, ...]


def reviewed_audio_sources(memberships, scans) -> list[ReviewedAudio]:
    """Deduplicate completed, explicitly analyzed audio tracks in the saved review."""
    audio = {}
    for membership in memberships:
        scan = scans[membership.scan_id]
        if (
            membership.role not in {"audio", "both"}
            or scan.origin != "generated"
            or not scan.scan_audio
            or not scan.analyzed_audio_selection_json
        ):
            continue
        try:
            selection = AudioSelection.model_validate_json(
                scan.analyzed_audio_selection_json
            ).selection()
        except ValueError:
            continue
        identity = selection.extraction_cache_key()
        if identity not in audio:
            language = normalize_language(selection.track.language)
            title = selection.track.title or language or "Audio"
            title = " ".join(title.split())[:128]
            label = f"{title} · Track {selection.track.stream_index}"
            audio[identity] = ReviewedAudio(
                ReviewAudioSource(id=identity, label=label, language=language), selection, ()
            )
        entry = audio[identity]
        if scan.audio_model not in entry.models:
            audio[identity] = replace(entry, models=(*entry.models, scan.audio_model))
    return sorted(
        audio.values(), key=lambda entry: (entry.selection.track.stream_index, entry.source.id)
    )


def _time(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid cached transcript time")
    return float(value)


def _cache_paths(audio: ReviewedAudio):
    for model in audio.models:
        try:
            key = transcription_cache_model_key(model)
        except ValueError:
            continue
        path = transcription_cache_path(audio.source.id, key, create=False)
        yield path
        backend = key.split("_", 1)[0]
        # Backend names use a hyphen internally. Try other model-variant caches for this track.
        yield from sorted(path.parent.glob(f"transcript_{backend}_*.json"), reverse=True)


def cached_segments(audio: ReviewedAudio) -> list[dict] | None:
    """Find a usable transcript only in the cache scoped to the selected physical track."""
    for path in dict.fromkeys(_cache_paths(audio)):
        try:
            data = json.loads(read_local_bytes(path, _MAX_TRANSCRIPT_BYTES))
            if not isinstance(data, list) or not data or len(data) > 50_000:
                continue
            if all(
                isinstance(segment, dict)
                and isinstance(segment.get("text"), str)
                and _time(segment.get("end_seconds")) > _time(segment.get("start_seconds"))
                for segment in data
            ):
                return data
        except (ValueError, OSError, UnicodeError):
            continue
    return None


def _words(segment: dict, offset: float) -> tuple[tuple[str, float, float], ...]:
    words = segment.get("words")
    if not isinstance(words, list):
        return ()
    try:
        return tuple(
            (
                word["word"],
                _time(word["start_seconds"]) + offset,
                _time(word["end_seconds"]) + offset,
            )
            for word in words
            if isinstance(word["word"], str)
        )
    except (KeyError, ValueError, TypeError):
        return ()


def transcript_document(
    media: Path, audio: ReviewedAudio, segments: list[dict]
) -> SubtitleDocument:
    """Use exact analyzed audio and shift its cached WAV timeline to movie playback."""
    from av.error import FFmpegError

    try:
        probe_audio_tracks(media).revalidate(audio.selection)
        offset = float(audio_playback_start(media, audio.selection, bounded=True))
    except FFmpegError as exc:
        raise ValueError("The selected audio timeline could not be verified") from exc
    srt = []
    timings = []
    for segment in segments:
        start = max(0, _time(segment["start_seconds"]) + offset)
        end = _time(segment["end_seconds"]) + offset
        text = " ".join(segment["text"].split())
        if round(start * 1000) >= round(end * 1000) or not text:
            continue
        # Transcript speech is literal; leave a valid plain cue even if it contains markup syntax.
        timings.append(_words(segment, offset))
        srt.append(
            f"{len(srt) + 1}\n{srt_time(start)} --> {srt_time(end)}\n"
            f"{html.escape(text, quote=False)}\n\n"
        )
    document = parse_subtitles("".join(srt).encode(), "srt")
    if not document.cues:
        raise ValueError("The cached transcript does not contain any timed dialogue")
    return replace(
        document,
        cues=tuple(
            replace(cue, words=times) for cue, times in zip(document.cues, timings, strict=True)
        ),
    )
