"""Audio content scanner using Whisper transcription + profanity detection."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from better_profanity import profanity as profanity

from scanner.evaluation.subcategorization import subcategorize_detector_label
from scanner.model_acquisition import acquire_backend
from scanner.models import FindingCreate, McfCategoryFamily, Severity
from scanner.runtime.runtime_support import resolve_ffmpeg
from scanner.settings.capabilities import DEFAULT_QWEN_MODEL, capability_for, current_environment
from scanner.settings.config import settings

from .audio_filters import (
    _audio_filter_detector as _audio_filter_detector,
)
from .audio_filters import (
    _coerce_glin_result as _coerce_glin_result,
)
from .audio_filters import (
    _contains_profanity_better_profanity as _contains_profanity_better_profanity,
)
from .audio_filters import (
    _load_alt_profanity_check_detector as _load_alt_profanity_check_detector,
)
from .audio_filters import (
    _load_glin_detector as _load_glin_detector,
)
from .audio_timing import audio_playback_offset as audio_playback_offset
from .audio_tracks import AudioTrackSelection, probe_audio_tracks
from .audio_transcription import (
    _load_transcript_chunk as _load_transcript_chunk,
)
from .audio_transcription import (
    _offset_and_trim_segments as _offset_and_trim_segments,
)
from .audio_transcription import (
    _serialize_transcript as _serialize_transcript,
)
from .audio_transcription import (
    _transcribe_serialized as _transcribe_serialized,
)
from .audio_transcription import (
    _transcribe_with_mlx_whisper as _transcribe_with_mlx_whisper,
)
from .audio_transcription import (
    _transcribe_with_resumable_chunks as _transcribe_with_resumable_chunks,
)
from .audio_transcription import (
    _transcription_with_cache as _transcription_with_cache,
)
from .audio_words import (
    _deny_matches_in_segment,
    _normalize_detected_word,
    _normalize_filter_word,
)
from .audio_words import (
    apply_audio_allow_defaults as apply_audio_allow_defaults,
)
from .audio_words import (
    effective_audio_filter_words as effective_audio_filter_words,
)
from .processes import run_command
from .qwen_language import categorize_language_findings


@dataclass
class ProfanityDetection:
    start_seconds: float
    end_seconds: float
    word: str
    text: str


_models: dict[str, Any] = {}
_audio_filter_detectors: dict[str, Callable[[str], bool]] = {}
_better_profanity_loaded = False

SUPPORTED_AUDIO_MODELS = {"whisper", "mlx_whisper"}
_AUDIO_MODEL_ALIASES = {
    "faster_whisper": "whisper",
    "faster-whisper": "whisper",
    "mlx-whisper": "mlx_whisper",
}

SUPPORTED_AUDIO_FILTERS = {"better_profanity", "glin_profanity", "alt_profanity_check", "qwen"}
_AUDIO_FILTER_ALIASES = {
    "better-profanity": "better_profanity",
    "betterprofanity": "better_profanity",
    "glin-profanity": "glin_profanity",
    "glinprofanity": "glin_profanity",
    "glin": "glin_profanity",
    "alt-profanity-check": "alt_profanity_check",
}

_TRANSCRIPT_CHUNK_SECONDS = 300
_TRANSCRIPT_CHUNK_OVERLAP_SECONDS = 1


def resolve_whisper_model_name(model_name: str | None = None) -> str:
    """Normalize a user-facing model selection to a Faster-Whisper identifier."""
    selected_model = model_name or settings.whisper_model
    # The public ``whisper`` selection uses Faster-Whisper's base model.
    return "base" if selected_model == "whisper" else selected_model


def resolve_audio_model_name(model_name: str | None = None) -> str:
    """Resolve a public audio model identifier to a supported backend id."""
    selected = (model_name or "whisper").strip().lower()
    resolved = _AUDIO_MODEL_ALIASES.get(selected, selected)
    if resolved not in SUPPORTED_AUDIO_MODELS:
        raise ValueError(f"Unsupported audio model: {model_name}")
    return resolved


def transcription_cache_model_key(model_name: str | None = None) -> str:
    """Return a stable cache key scoped by backend and underlying model size."""
    selected_model = resolve_audio_model_name(model_name)
    model_variant = resolve_whisper_model_name()
    return f"{selected_model}_{model_variant}"


def resolve_mlx_whisper_model_name(model_name: str | None = None) -> str:
    """Return the MLX checkpoint path/repository for the selected Whisper size."""
    model_variant = resolve_whisper_model_name(model_name)
    # Explicit Hub IDs and local paths are already valid mlx-whisper targets.
    if "/" in model_variant or model_variant.startswith((".", "~")):
        return model_variant
    return f"mlx-community/whisper-{model_variant}-mlx"


def _get_whisper_model(model_name: str | None = None) -> Any:
    """Acquire and cache the configured Whisper model for local transcription."""
    from faster_whisper import WhisperModel

    resolved_model = resolve_whisper_model_name(model_name)
    if resolved_model != "base":
        raise ValueError(
            "Only the pinned base faster-whisper checkpoint is supported by this release."
        )
    if resolved_model not in _models:
        environment = current_environment()
        # CUDA is additive on Windows/Linux and is selected only after the capability
        # probe verifies the packaged CTranslate2 runtime against a host NVIDIA driver.
        # Every failed/absent probe stays on the guaranteed CPU path.
        device = (
            capability_for("whisper", environment=environment).device
            if environment.target_id in {"windows-x64", "linux-x86_64"}
            else "auto"
        )
        model_dir = acquire_backend("whisper")["faster-whisper-base"]
        _models[resolved_model] = WhisperModel(
            str(model_dir), device=device, compute_type="default"
        )
    return _models[resolved_model]


def _normalize_audio_filter(filter_name: str | None) -> str:
    selected = (filter_name or settings.audio_filter or "better_profanity").strip().lower()
    resolved = _AUDIO_FILTER_ALIASES.get(selected, selected)
    if resolved not in SUPPORTED_AUDIO_FILTERS:
        raise ValueError(f"Unsupported audio filter: {filter_name}")
    return resolved


def extract_audio(
    video_path: str,
    output_path: str,
    should_cancel: Callable[[], bool] | None = None,
    duration_seconds: float | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    *,
    audio_selection: AudioTrackSelection | None = None,
) -> str:
    """Extract audio track from video to WAV file."""
    if audio_selection is not None:
        probe_audio_tracks(video_path).revalidate(audio_selection)
    ffmpeg = resolve_ffmpeg()
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Keep a WAV suffix on the atomic temporary file.  FFmpeg chooses its muxer
    # from the output extension, so `audio.wav.partial` is not a WAV output.
    temporary_output_path = destination.with_name(
        f".{destination.stem}.partial{destination.suffix or '.wav'}"
    )
    temporary_output_path.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-i",
        video_path,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-progress",
        "pipe:2",
        "-nostats",
        "-f",
        "wav",
        str(temporary_output_path),
        "-y",
    ]
    if audio_selection is not None:
        input_end = command.index("-i") + 2
        command[input_end:input_end] = ["-map", f"0:{audio_selection.track.stream_index}"]

    def on_ffmpeg_output(line: str) -> None:
        """Convert FFmpeg progress output into a bounded extraction fraction."""
        if not progress_callback or not line.startswith("out_time_us="):
            return
        try:
            processed_seconds = int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return
        if duration_seconds is None:
            return
        fraction = min(max(processed_seconds / max(duration_seconds, 0.001), 0.0), 1.0)
        progress_callback(
            fraction,
            f"Extracting audio ({processed_seconds:.0f}s/{duration_seconds:.0f}s)",
        )

    try:
        if sys.platform == "darwin":
            input_index = command.index("-i")
            hw_command = [
                *command[:input_index],
                "-hwaccel",
                "videotoolbox",
                *command[input_index:],
            ]
            try:
                run_command(
                    hw_command,
                    should_cancel=should_cancel,
                    output_callback=on_ffmpeg_output,
                )
            except subprocess.CalledProcessError:
                run_command(
                    command,
                    should_cancel=should_cancel,
                    output_callback=on_ffmpeg_output,
                )
        else:
            run_command(command, should_cancel=should_cancel, output_callback=on_ffmpeg_output)
        if not temporary_output_path.is_file() or temporary_output_path.stat().st_size == 0:
            raise RuntimeError("Audio extraction produced no WAV output")
        if audio_selection is not None:
            probe_audio_tracks(video_path).revalidate(audio_selection)
        os.replace(temporary_output_path, destination)
        return output_path
    finally:
        temporary_output_path.unlink(missing_ok=True)


def scan_audio(
    audio_path: str,
    video_id: str,
    model_name: str | None = None,
    filter_name: str | None = None,
    deny_words: list[str] | None = None,
    transcript_cache_path: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    qwen_model: str | None = None,
    provenance_callback: Callable[[str], None] | None = None,
) -> list[FindingCreate]:
    """
    Transcribe audio and detect profanity with timestamps.

    Uses the selected transcription backend for word-level timestamps,
    then checks each word/segment against the profanity filter.
    """
    requested_filter = (filter_name or settings.audio_filter or "better_profanity").strip().lower()
    selected_filter = _AUDIO_FILTER_ALIASES.get(requested_filter, requested_filter)
    if selected_filter == "qwen":
        qwen_model = qwen_model or DEFAULT_QWEN_MODEL
    contains_profanity = (
        (lambda _text: False)
        if selected_filter == "qwen"
        else _audio_filter_detector(selected_filter)
    )
    _runtime_allow_unused, runtime_deny = effective_audio_filter_words([], deny_words)
    transcript_segments = _transcription_with_cache(
        audio_path,
        model_name,
        transcript_cache_path=transcript_cache_path,
        progress_callback=(
            (lambda fraction, message: progress_callback(fraction * 0.8, message))
            if qwen_model and progress_callback
            else progress_callback
        ),
    )

    detections: list[ProfanityDetection] = []

    for segment in transcript_segments:
        words = segment.get("words") or []
        if words:
            for word_info in words:
                raw_word = str(word_info.get("word", "")).strip()
                normalized_word = _normalize_detected_word(raw_word)
                if not normalized_word:
                    continue
                raw_word_lower = _normalize_filter_word(raw_word)
                if (
                    contains_profanity(raw_word_lower)
                    or contains_profanity(normalized_word)
                    or normalized_word in runtime_deny
                ):
                    detections.append(
                        ProfanityDetection(
                            start_seconds=max(
                                0,
                                float(word_info["start_seconds"]) - settings.profanity_pad_seconds,
                            ),
                            end_seconds=float(word_info["end_seconds"])
                            + settings.profanity_pad_seconds,
                            word=normalized_word,
                            text=str(segment.get("text", "")).strip(),
                        )
                    )
        else:
            segment_text = str(segment.get("text", "")).strip()
            segment_start = max(
                0,
                float(segment.get("start_seconds", 0.0)) - settings.profanity_pad_seconds,
            )
            segment_end = float(segment.get("end_seconds", 0.0)) + settings.profanity_pad_seconds
            deny_matches = _deny_matches_in_segment(segment_text, runtime_deny)
            for deny_word in deny_matches:
                detections.append(
                    ProfanityDetection(
                        start_seconds=segment_start,
                        end_seconds=segment_end,
                        word=deny_word,
                        text=segment_text,
                    )
                )
            if not deny_matches and contains_profanity(segment_text):
                detections.append(
                    ProfanityDetection(
                        start_seconds=segment_start,
                        end_seconds=segment_end,
                        word="[segment]",
                        text=segment_text,
                    )
                )

    findings = []
    for detection in detections:
        classification = subcategorize_detector_label(
            producer=selected_filter,
            category=McfCategoryFamily.LANGUAGE,
            label=detection.word,
            confidence=0.9,
        )
        findings.append(
            FindingCreate(
                video_id=video_id,
                category=classification.category,
                subcategory=classification.subcategory,
                start_seconds=detection.start_seconds,
                end_seconds=detection.end_seconds,
                severity=Severity.MEDIUM,
                confidence=0.9,
                label=detection.word,
                details=detection.text,
                source="audio",
            )
        )
    if qwen_model:
        findings = categorize_language_findings(
            findings,
            qwen_model,
            transcript_segments=transcript_segments if selected_filter == "qwen" else None,
            video_id=video_id,
            **({"provenance_callback": provenance_callback} if provenance_callback else {}),
            progress_callback=(
                (lambda fraction, message: progress_callback(0.8 + fraction * 0.2, message))
                if progress_callback
                else None
            ),
        )
        if progress_callback:
            progress_callback(1.0, "Audio analysis complete")
    return findings
