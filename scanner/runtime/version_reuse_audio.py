"""Read authorized physical audio selections for dialogue verification."""

from pathlib import Path

from av.error import FFmpegError

from scanner.modeling.cue_alignment import AudioCue, infer_transcript_offset
from scanner.modeling.model_subtitles import ReviewAudioSource
from scanner.modeling.version_alignment import transcript_anchors, transcript_words
from scanner.persistence.repository.repository_audio_tracks import recorded_playback_offset
from scanner.pipeline.audio_timing import audio_playback_start
from scanner.pipeline.audio_tracks import AudioTrackSelection, probe_audio_tracks

from .subtitle_transcript import ReviewedAudio, cached_segments
from .transcript_evidence import (
    analyzed_selection as analyzed_selection,
)
from .transcript_evidence import scan_transcript_candidates


class TranscriptAlignmentUnavailable(ValueError):
    """The cached speech or its playback clock cannot be verified."""


def select_target_audio(path: Path, track_index: int | None) -> AudioTrackSelection:
    """Select the intended source track using the standard local audio inventory."""
    try:
        return probe_audio_tracks(path).select(track_index)
    except (OSError, FFmpegError, ValueError) as exc:
        raise ValueError("Choose an available audio track for the target version") from exc


def _cached_segments(selection, candidates):
    key = selection.extraction_cache_key()
    audio = ReviewedAudio(ReviewAudioSource(id=key, label="Audio"), selection, candidates)
    return cached_segments(audio)


def target_transcript_ready(selection, candidates):
    """Check for timed words without decoding audio or generating a transcript."""
    segments = _cached_segments(selection, candidates)
    return bool(segments and transcript_words(segments, 0))


def _words(path, selection, candidates, *, subject, playback_offset=None, cache_selection=None):
    segments = _cached_segments(cache_selection or selection, candidates)
    if segments is None:
        raise TranscriptAlignmentUnavailable(f"The {subject} audio track needs a cached transcript")
    try:
        if playback_offset is None:
            probe_audio_tracks(path).revalidate(selection)
            playback_offset = float(audio_playback_start(path, selection, bounded=True))
        words = transcript_words(segments, playback_offset)
    except (OSError, FFmpegError, ValueError) as exc:
        raise TranscriptAlignmentUnavailable(
            f"The {subject} audio playback clock could not be verified"
        ) from exc
    if not words:
        raise TranscriptAlignmentUnavailable(f"The {subject} transcript needs word timestamps")
    return words


def target_transcript_words(path, selection, candidates, cache_selection=None):
    """Use only the timed transcript of the selected target file and track."""
    return _words(path, selection, candidates, subject="target", cache_selection=cache_selection)


def original_playback_offset(scan, selection, cues: list[AudioCue]) -> float | None:
    """Use the recorded or measurable clock, or infer it from independent old flags."""
    recorded = recorded_playback_offset(scan, selection)
    if recorded is not None:
        return recorded
    try:
        path = Path(scan.resolved_local_path)
        probe_audio_tracks(path).revalidate(selection)
        return float(audio_playback_start(path, selection, bounded=True))
    except (OSError, FFmpegError, ValueError):
        segments = _cached_segments(selection, scan_transcript_candidates(scan))
        return infer_transcript_offset(cues, transcript_words(segments, 0)) if segments else None


def saved_audio_cues(findings):
    """Retain only timed audio mutes, with any text carried by their source."""
    return [
        AudioCue(
            finding.start_seconds, finding.end_seconds, finding.label or "", finding.details or ""
        )
        for finding in findings
        if finding.end_seconds is not None
        and finding.edit_action == "mute"
        and (
            finding.mcf_channel == "audio"
            or (finding.source == "audio" and finding.mcf_channel != "video")
        )
    ]


def match_audio_transcripts(
    scan, target_path, target_selection, candidates, *, target_words=None, original_offset=None
):
    """Line up cached source and target words on their verified playback clocks."""
    source_selection = analyzed_selection(scan)
    if source_selection is None:
        raise TranscriptAlignmentUnavailable("This scan has no recorded analyzed audio track")
    source = _words(
        Path(scan.resolved_local_path),
        source_selection,
        scan_transcript_candidates(scan),
        subject="original",
        playback_offset=(
            original_offset
            if original_offset is not None
            else recorded_playback_offset(scan, source_selection)
        ),
    )
    target = (
        target_words
        if target_words is not None
        else target_transcript_words(target_path, target_selection, candidates)
    )
    anchors = transcript_anchors(source, target)
    if not anchors:
        raise TranscriptAlignmentUnavailable(
            "The selected transcripts have no reliable dialogue matches"
        )
    return anchors
