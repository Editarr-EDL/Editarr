"""Selected audio extraction, analysis, checkpoints, and actual-input recording."""

from .processes import ScanCancelledError


def run_audio_stages(
    db,
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
    checkpoint_callback,
    analyzed_audio_callback,
    findings_writer,
    record_stage,
    emit,
):
    """Run one track through existing pipeline adapters and provenance callbacks."""
    from . import runner as pipeline
    from .audio_tracks import probe_audio_tracks

    identity = audio_selection.extraction_cache_key() if audio_selection else video_hash
    shared_audio_path = pipeline.cached_audio_path(identity)
    selected_model = pipeline.transcription_cache_model_key(audio_model)
    transcript_path = pipeline.transcription_cache_path(identity, selected_model)
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
            if checkpoint_callback:
                checkpoint_callback("audio_extract")
        except BaseException as exc:
            record_stage(
                "audio_extract", "partial" if isinstance(exc, ScanCancelledError) else "failed"
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
    playback_offset = 0.0
    if audio_selection is not None:
        from .audio import audio_playback_offset

        playback_offset = audio_playback_offset(
            str(path), audio_selection, should_cancel=should_cancel
        )
    pipeline.logger.info("Transcribing and scanning for profanity...")
    emit("audio_scan", 0.0, "Transcribing audio")
    record_stage("audio_scan", "started")
    record_stage("audio_qwen", "requested")
    try:
        findings = pipeline._scan_audio_isolated(
            str(shared_audio_path),
            video_id,
            audio_model=audio_model,
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
        if findings:
            findings_writer(db, video_id, findings)
        if checkpoint_callback:
            checkpoint_callback("audio_scan")
    except BaseException as exc:
        record_stage("audio_scan", "partial" if isinstance(exc, ScanCancelledError) else "failed")
        raise
    record_stage("audio_scan", "complete")
    pipeline.logger.info("Found %d audio findings", len(findings))
