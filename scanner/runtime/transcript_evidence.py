"""Identify the transcript a completed scan produced for its recorded audio track."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.persistence.repository.repository_provenance import completed_stage_settings
from scanner.pipeline.audio_tracks import AudioTrackSelection
from scanner.settings.capabilities import APPLE_SPEECH_MODEL

_APPLE_CACHE_KEY = re.compile(rf"{APPLE_SPEECH_MODEL}_[a-z0-9.-]{{1,64}}")


@dataclass(frozen=True)
class RecordedTranscript:
    selection_key: str
    model_key: str


TranscriptCandidate = RecordedTranscript | str | None


def analyzed_selection(scan) -> AudioTrackSelection | None:
    """Read the recorded analyzed track, with no default-track fallback."""
    if not scan.analyzed_audio_selection_json:
        return None
    try:
        return AudioSelection.model_validate_json(scan.analyzed_audio_selection_json).selection()
    except ValueError:
        return None


def scan_transcript_candidates(scan) -> tuple[TranscriptCandidate, ...]:
    """Scope Apple Speech to the completed attempt and retain legacy Whisper IDs."""
    model = scan.audio_model
    if model != APPLE_SPEECH_MODEL:
        return (model,)
    if scan.origin != "generated" or scan.status != "complete":
        return ()
    selection = analyzed_selection(scan)
    settings = completed_stage_settings(scan, "audio_scan")
    if (
        selection is None
        or settings is None
        or settings.get("audio_selection") != asdict(selection)
    ):
        return ()
    key = settings.get("transcript_cache_key")
    if not isinstance(key, str) or not _APPLE_CACHE_KEY.fullmatch(key):
        return ()
    return (RecordedTranscript(selection.extraction_cache_key(), key),)
