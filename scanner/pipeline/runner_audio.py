"""Selected audio extraction, analysis, checkpoints, and actual-input recording."""

from scanner.runtime.worker_quit import FullApplicationQuitRequested
from scanner.settings.capabilities import APPLE_SPEECH_MODEL

from .processes import ScanCancelledError


def run_audio_stages(
    path,
    *,
    video_hash,
    duration,
    video_id,
    audio_model,
    audio_filter,
    audio_padding_seconds,
    qwen_model,
    audio_selection,
    should_cancel,
    completed_stages,
    save_stage_results,
    analyzed_audio_callback,
    analyzed_audio_timing_callback,
    record_stage,
    emit,
):
    """Run one track through existing pipeline adapters and provenance callbacks."""
    from . import runner as pipeline
    from .apple_speech import speech_locale
    from .audio import resolve_audio_model_name
    from .audio_tracks import probe_audio_tracks

    identity = audio_selection.extraction_cache_key() if audio_selection else video_hash
    shared_audio_path = pipeline.cached_audio_path(identity)
    if should_cancel and should_cancel():
        raise ScanCancelledError("Scan cancelled")
    if not shared_audio_path.exists():
        pipeline.logger.info("Extracting audio...")
        emit("audio_extract", 0.0, "Extracting audio")
        record_stage("audio_extract", "started")
        try:
            pipeline.extract_audio(
                str(path),
                str(shared_audio_path),
                should_cancel=should_cancel,
                duration_seconds=duration,
                progress_callback=lambda fraction, message: emit(
                    "audio_extract", fraction, message
                ),
                **({"audio_selection": audio_selection} if audio_selection else {}),
            )
            save_stage_results("audio_extract", [])
        except BaseException as exc:
            record_stage(
                "audio_extract", "partial" if isinstance(
                    exc, (ScanCancelledError, FullApplicationQuitRequested)
                ) else "failed"
            )
            raise
        record_stage("audio_extract", "complete")
    else:
        pipeline.logger.info("Using cached audio extraction for identity %s", identity)
        record_stage("audio_extract", "reused")
        emit("audio_extract", 1.0, "Using cached audio extraction")
    if should_cancel and should_cancel():
        raise ScanCancelledError("Scan cancelled")
    emit("audio_extract", 1.0, "Audio extraction complete")
    if audio_selection is not None:
        probe_audio_tracks(path).revalidate(audio_selection)
    if "audio_scan" in completed_stages:
        emit("audio_scan", 1.0, "Audio analysis restored from checkpoint")
        return
    apple_locale = (
        speech_locale(audio_selection.track.language if audio_selection else None)
        if resolve_audio_model_name(audio_model) == APPLE_SPEECH_MODEL
        else None
    )
    transcript_key = pipeline.transcription_cache_model_key(
        audio_model, apple_speech_locale=apple_locale
    )
    transcript_path = pipeline.transcription_cache_path(identity, transcript_key)
    playback_offset = 0.0
    if audio_selection is not None:
        from .audio import audio_playback_offset

        playback_offset = audio_playback_offset(
            str(path), audio_selection, should_cancel=should_cancel
        )
    pipeline.logger.info("Transcribing and scanning for profanity...")
    emit("audio_scan", 0.0, "Transcribing audio")
    stage_settings = {"transcript_cache_key": transcript_key} if apple_locale else {}
    record_stage("audio_scan", "started", **stage_settings)
    record_stage("audio_qwen", "requested")
    try:
        findings = pipeline._scan_audio_isolated(
            str(shared_audio_path),
            video_id,
            audio_model=audio_model,
            apple_speech_locale=apple_locale,
            audio_filter=audio_filter,
            audio_padding_seconds=audio_padding_seconds,
            qwen_model=qwen_model,
            transcript_cache_path_value=str(transcript_path),
            progress_callback=lambda fraction, message: emit("audio_scan", fraction, message),
            provenance_callback=lambda state: record_stage("audio_qwen", state),
            should_cancel=should_cancel,
        )
        if playback_offset:
            findings = [
                finding.model_copy(
                    update={
                        "start_seconds": finding.start_seconds + playback_offset,
                        "end_seconds": finding.end_seconds + playback_offset,
                    }
                )
                for finding in findings
            ]
        emit("audio_scan", 1.0, "Audio analysis complete")
        if audio_selection is not None:
            probe_audio_tracks(path).revalidate(audio_selection)
            if analyzed_audio_callback:
                analyzed_audio_callback(audio_selection)
            if analyzed_audio_timing_callback:
                analyzed_audio_timing_callback(audio_selection, playback_offset)
        save_stage_results("audio_scan", findings)
    except BaseException as exc:
        record_stage("audio_scan", "partial" if isinstance(
            exc, (ScanCancelledError, FullApplicationQuitRequested)
        ) else "failed")
        raise
    record_stage("audio_scan", "complete")
    pipeline.logger.info("Found %d audio findings", len(findings))
