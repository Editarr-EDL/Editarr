"""Timed transcript serialization, caching, and resumable WAV chunks."""

import json
import wave
from collections.abc import Callable
from importlib import import_module
from inspect import signature
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scanner.pipeline.cache import atomic_write_json
from scanner.settings.capabilities import APPLE_SPEECH_MODEL

from . import apple_speech

Transcriber = Callable[[str], list[dict[str, Any]]]


def _serialize_transcript(segments: Any) -> list[dict[str, Any]]:

    """Convert model segments and word timestamps into cacheable dictionaries."""
    def _segment_attr(segment: Any, key: str, default: Any = None) -> Any:
        if isinstance(segment, dict):
            return segment.get(key, default)
        return getattr(segment, key, default)

    def _word_attr(word: Any, key: str, default: Any = None) -> Any:
        if isinstance(word, dict):
            return word.get(key, default)
        return getattr(word, key, default)

    serialized: list[dict[str, Any]] = []
    for segment in segments:
        words = []
        segment_words = _segment_attr(segment, "words") or []
        if segment_words:
            for word_info in segment_words:
                word_start = _word_attr(word_info, "start")
                word_end = _word_attr(word_info, "end")
                if word_start is None or word_end is None:
                    continue
                words.append(
                    {
                        "word": str(_word_attr(word_info, "word", "")),
                        "start_seconds": float(word_start),
                        "end_seconds": float(word_end),
                    }
                )
        segment_start = _segment_attr(segment, "start", None)
        segment_end = _segment_attr(segment, "end", None)
        if segment_start is None and words:
            segment_start = words[0]["start_seconds"]
        if segment_end is None and words:
            segment_end = words[-1]["end_seconds"]
        serialized.append(
            {
                "text": str(_segment_attr(segment, "text", "")).strip(),
                "start_seconds": float(segment_start or 0.0),
                "end_seconds": float(segment_end or 0.0),
                "words": words,
            }
        )
    return serialized


def _transcribe_with_mlx_whisper(audio_path: str) -> list[dict[str, Any]]:
    """Transcribe audio with the pinned MLX checkpoint and normalize its segments."""
    from scanner.pipeline import audio as root

    try:
        mlx_whisper = import_module("mlx_whisper")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "mlx-whisper audio model is selected but mlx-whisper is not installed"
        ) from exc

    transcribe = getattr(mlx_whisper, "transcribe", None)
    if not callable(transcribe):
        raise RuntimeError("mlx-whisper does not expose a callable transcribe() API")

    model_variant = root.resolve_whisper_model_name()
    if model_variant != "base":
        raise ValueError(
            "Only the pinned base MLX Whisper checkpoint is supported by this release."
        )
    model_path = root.acquire_backend("mlx_whisper")["mlx-whisper-base"]
    kwargs: dict[str, Any] = {}
    try:
        params = signature(transcribe).parameters
    except (TypeError, ValueError):
        params = {}
    accepts_kwargs = any(parameter.kind.name == "VAR_KEYWORD" for parameter in params.values())
    if "path_or_hf_repo" in params or accepts_kwargs:
        kwargs["path_or_hf_repo"] = str(model_path)
    elif "model" in params:
        kwargs["model"] = str(model_path)
    if "word_timestamps" in params or accepts_kwargs:
        kwargs["word_timestamps"] = True

    result = transcribe(audio_path, **kwargs)

    if isinstance(result, dict):
        segments = result.get("segments")
    else:
        segments = getattr(result, "segments", None)
    if segments is None:
        raise RuntimeError("mlx-whisper transcription response missing segments")
    return root._serialize_transcript(segments)


def _transcription_with_cache(
    audio_path: str,
    model_name: str | None,
    transcript_cache_path: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    *,
    transcribe: Transcriber | None = None,
) -> list[dict[str, Any]]:
    """Read a cached transcript or transcribe and atomically cache the result."""
    from scanner.pipeline import audio as root

    if transcript_cache_path:
        cache_path = Path(transcript_cache_path)
        if cache_path.exists():
            try:
                with cache_path.open("r", encoding="utf-8") as file_obj:
                    loaded = json.load(file_obj)
                if isinstance(loaded, list):
                    if progress_callback:
                        progress_callback(1.0, "Using cached transcript")
                    return loaded
            except (OSError, json.JSONDecodeError):
                pass

    serialized = root._transcribe_with_resumable_chunks(
        audio_path,
        model_name,
        transcript_cache_path,
        progress_callback=progress_callback,
        transcribe=transcribe,
    )

    if transcript_cache_path:
        cache_path = Path(transcript_cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(cache_path, serialized)

    return serialized


def _transcribe_serialized(
    audio_path: str,
    model_name: str | None,
    *,
    locale: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    from scanner.pipeline import audio as root

    selected_model = root.resolve_audio_model_name(model_name)
    if selected_model == APPLE_SPEECH_MODEL:
        return root._serialize_transcript(
            apple_speech.transcribe(
                audio_path, locale=locale or apple_speech.speech_locale(),
                should_cancel=should_cancel,
            )
        )
    if selected_model == "mlx_whisper":
        return root._transcribe_with_mlx_whisper(audio_path)
    model = root._get_whisper_model(model_name)
    segments, _info = model.transcribe(audio_path, word_timestamps=True)
    return root._serialize_transcript(segments)


def _transcribe_with_resumable_chunks(
    audio_path: str,
    model_name: str | None,
    transcript_cache_path: str | None,
    progress_callback: Callable[[float, str], None] | None = None,
    *,
    transcribe: Transcriber | None = None,
) -> list[dict[str, Any]]:
    """Transcribe PCM WAV in reusable, overlapped chunks when a cache is available."""
    from scanner.pipeline import audio as root

    trim_timed_text = root.resolve_audio_model_name(model_name) == APPLE_SPEECH_MODEL

    def transcribe_file(path: str) -> list[dict[str, Any]]:
        return transcribe(path) if transcribe else root._transcribe_serialized(path, model_name)

    if not transcript_cache_path:
        transcript = transcribe_file(audio_path)
        if progress_callback:
            progress_callback(1.0, "Audio transcription complete")
        return transcript
    try:
        with wave.open(audio_path, "rb") as source:
            if source.getcomptype() != "NONE":
                transcript = transcribe_file(audio_path)
                if progress_callback:
                    progress_callback(1.0, "Audio transcription complete")
                return transcript
            frame_rate = source.getframerate()
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            total_frames = source.getnframes()
    except (OSError, wave.Error):
        transcript = transcribe_file(audio_path)
        if progress_callback:
            progress_callback(1.0, "Audio transcription complete")
        return transcript

    cache_path = Path(transcript_cache_path)
    chunk_dir = cache_path.with_suffix("").with_name(f".{cache_path.stem}.chunks")
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_frames = frame_rate * root._TRANSCRIPT_CHUNK_SECONDS
    overlap_frames = frame_rate * root._TRANSCRIPT_CHUNK_OVERLAP_SECONDS
    transcript: list[dict[str, Any]] = []
    total_chunks = max(1, (total_frames + chunk_frames - 1) // chunk_frames)

    for index, primary_start in enumerate(range(0, total_frames, chunk_frames)):
        primary_end = min(primary_start + chunk_frames, total_frames)
        source_start = max(0, primary_start - overlap_frames)
        source_end = min(total_frames, primary_end + overlap_frames)
        chunk_cache = chunk_dir / f"chunk_{index:06d}.json"
        chunk_segments = root._load_transcript_chunk(chunk_cache)
        if chunk_segments is None:
            with TemporaryDirectory(dir=chunk_dir) as temporary:
                temporary_audio = str(Path(temporary) / "audio.wav")
                with (
                    wave.open(audio_path, "rb") as source,
                    wave.open(temporary_audio, "wb") as target,
                ):
                    target.setnchannels(channels)
                    target.setsampwidth(sample_width)
                    target.setframerate(frame_rate)
                    source.setpos(source_start)
                    target.writeframes(source.readframes(source_end - source_start))
                chunk_segments = transcribe_file(temporary_audio)
            chunk_segments = root._offset_and_trim_segments(
                chunk_segments,
                source_start / frame_rate,
                primary_start / frame_rate,
                primary_end / frame_rate,
                primary_end == total_frames,
                trim_timed_text=trim_timed_text,
            )
            atomic_write_json(chunk_cache, chunk_segments)
        transcript.extend(chunk_segments)
        if progress_callback:
            progress_callback(
                primary_end / max(total_frames, 1),
                f"Transcribed audio chunk {index + 1}/{total_chunks}",
            )
    return transcript


def _load_transcript_chunk(path: Path) -> list[dict[str, Any]] | None:

    try:
        with path.open("r", encoding="utf-8") as file_obj:
            value = json.load(file_obj)
        return value if isinstance(value, list) else None
    except (OSError, json.JSONDecodeError):
        return None


def _offset_and_trim_segments(
    segments: list[dict[str, Any]],
    offset: float,
    primary_start: float,
    primary_end: float,
    is_final_chunk: bool,
    *,
    trim_timed_text: bool = False,
) -> list[dict[str, Any]]:

    """Shift chunk timestamps and retain words belonging to the chunk primary span."""
    trimmed: list[dict[str, Any]] = []
    for segment in segments:
        words = []
        for word in segment.get("words") or []:
            start = float(word["start_seconds"]) + offset
            end = float(word["end_seconds"]) + offset
            midpoint = (start + end) / 2
            if primary_start <= midpoint and (is_final_chunk or midpoint < primary_end):
                words.append({**word, "start_seconds": start, "end_seconds": end})
        start = float(segment.get("start_seconds", 0.0)) + offset
        end = float(segment.get("end_seconds", start)) + offset
        midpoint = (start + end) / 2
        belongs_to_primary_chunk = primary_start <= midpoint and (
            is_final_chunk or midpoint < primary_end
        )
        if words or (not segment.get("words") and belongs_to_primary_chunk):
            if trim_timed_text and words and len(words) != len(segment["words"]):
                segment = {**segment, "text": "".join(word["word"] for word in words).strip()}
                start, end = words[0]["start_seconds"], words[-1]["end_seconds"]
            trimmed.append({**segment, "start_seconds": start, "end_seconds": end, "words": words})
    return trimmed


