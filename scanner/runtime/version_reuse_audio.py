"""Read authorized physical audio selections for dialogue verification."""

from pathlib import Path

from av.error import FFmpegError

from scanner.modeling.cue_alignment import AudioCue
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.modeling.model_subtitles import ReviewAudioSource
from scanner.modeling.version_alignment import transcript_anchors, transcript_words
from scanner.persistence.repository.repository_audio_tracks import recorded_playback_offset
from scanner.pipeline.audio_timing import audio_playback_start
from scanner.pipeline.audio_tracks import AudioTrackSelection, probe_audio_tracks

from .subtitle_transcript import ReviewedAudio, cached_segments


class TranscriptAlignmentUnavailable(ValueError):
    """The cached speech or its playback clock cannot be verified."""


def analyzed_selection(scan) -> AudioTrackSelection | None:
    """Read the recorded analyzed track, with no default-track fallback."""
    if not scan.analyzed_audio_selection_json:
        return None
    try:
        return AudioSelection.model_validate_json(scan.analyzed_audio_selection_json).selection()
    except ValueError:
        return None


def select_target_audio(path: Path, track_index: int | None) -> AudioTrackSelection:
    """Select the intended source track using the standard local audio inventory."""
    try:
        return probe_audio_tracks(path).select(track_index)
    except (OSError, FFmpegError, ValueError) as exc:
        raise ValueError("Choose an available audio track for the target version") from exc


def _cached_segments(selection, model_names):
    key = selection.extraction_cache_key()
    audio = ReviewedAudio(ReviewAudioSource(id=key, label="Audio"), selection, model_names)
    return cached_segments(audio)


def target_transcript_ready(selection, model_names):
    """Check for timed words without decoding audio or generating a transcript."""
    segments = _cached_segments(selection, model_names)
    return bool(segments and transcript_words(segments, 0))


def _words(path, selection, model_names, *, subject, playback_offset=None):
    segments = _cached_segments(selection, model_names)
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


def target_transcript_words(path, selection, model_names):
    """Use only the timed transcript of the selected target file and track."""
    return _words(path, selection, model_names, subject="target")


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


def match_audio_transcripts(scan, target_path, target_selection, model_names, *, target_words=None):
    """Line up cached source and target words on their verified playback clocks."""
    source_selection = analyzed_selection(scan)
    if source_selection is None:
        raise TranscriptAlignmentUnavailable("This scan has no recorded analyzed audio track")
    source = _words(
        Path(scan.resolved_local_path),
        source_selection,
        (scan.audio_model,),
        subject="original",
        playback_offset=recorded_playback_offset(scan, source_selection),
    )
    target = (
        target_words
        if target_words is not None
        else target_transcript_words(target_path, target_selection, model_names)
    )
    anchors = transcript_anchors(source, target)
    if not anchors:
        raise TranscriptAlignmentUnavailable(
            "The selected transcripts have no reliable dialogue matches"
        )
    return anchors
