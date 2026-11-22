"""Read authorized audio evidence for shared scan and import verification."""

from dataclasses import replace

from sqlalchemy import and_, or_

from scanner.db import MediaItemRow, ScanRow
from scanner.modeling.cue_alignment import cue_anchors, match_unmapped_dialogue
from scanner.modeling.version_alignment import Alignment
from scanner.persistence.repository.repository_audio_tracks import recorded_playback_offset
from scanner.persistence.repository.scan_jobs import scan_job_facts
from scanner.persistence.repository.version_reuse import source_findings
from scanner.persistence.rows.db_media_identity import MediaVersionRow, ScanMediaVersionRow
from scanner.runtime.verification_waveform import match_cached_audio_cues
from scanner.runtime.version_reuse_audio import (
    TranscriptAlignmentUnavailable,
    analyzed_selection,
    match_audio_transcripts,
    original_playback_offset,
    saved_audio_cues,
    target_transcript_ready,
    target_transcript_words,
)
from scanner.security.access.decisions import AccessDenied


def _visible_target_audio(caller, target, selection, digest=None):
    """Find authorized recordings of the selected stream at known file locations."""
    db = caller.db
    locations = [
        and_(
            MediaItemRow.jellyfin_id == target.source.item_id,
            ScanRow.media_source_id == target.source.media_source_id,
        ),
        ScanRow.resolved_local_path == target.media.resolved_path,
    ]
    if digest:
        locations.append(MediaVersionRow.content_sha256 == digest)
    candidates = (
        db.query(ScanRow, MediaVersionRow.content_sha256)
        .join(MediaItemRow, MediaItemRow.id == ScanRow.media_item_id)
        .outerjoin(ScanMediaVersionRow, ScanMediaVersionRow.scan_id == ScanRow.id)
        .outerjoin(MediaVersionRow, MediaVersionRow.id == ScanMediaVersionRow.version_id)
        .filter(
            MediaItemRow.server_id == target.source.server_id,
            or_(*locations),
            ScanRow.status == "complete",
            ScanRow.origin == "generated",
            ScanRow.scan_audio.is_(True),
            ScanRow.analyzed_audio_selection_json.is_not(None),
        )
        .order_by(ScanRow.created_at.desc(), ScanRow.id)
        .limit(100)
    )
    matches = []
    for scan, digest in candidates:
        analyzed = analyzed_selection(scan)
        if analyzed and analyzed.track == selection.track:
            matches.append((scan, analyzed, digest))
    facts = scan_job_facts(db, [scan.id for scan, _, _ in matches])
    for scan, analyzed, digest in matches:
        try:
            caller.require_scan(facts[scan.id], caller.media_decision(facts[scan.id].media))
        except AccessDenied:
            continue
        yield scan, analyzed, digest


def _target_audio_scans(caller, target, selection, digest=None):
    """Require the same local generation or independently verified file bytes."""
    for scan, analyzed, pinned in _visible_target_audio(caller, target, selection, digest):
        if analyzed == selection or (digest is not None and pinned == digest):
            yield scan


def _audio_reference(caller, source, source_version, target_audio):
    """Use the source's track or a visible generated scan on its exact version."""
    if analyzed_selection(source) is not None:
        return source
    if source.scan_audio:
        return None
    rows = (
        caller.db.query(ScanRow)
        .join(ScanMediaVersionRow, ScanMediaVersionRow.scan_id == ScanRow.id)
        .filter(
            ScanMediaVersionRow.version_id == source_version.id,
            ScanRow.status == "complete",
            ScanRow.origin == "generated",
            ScanRow.analyzed_audio_selection_json.is_not(None),
        )
        .order_by(ScanRow.created_at.desc())
        .limit(30)
        .all()
    )
    facts = scan_job_facts(caller.db, [row.id for row in rows])
    matches = {}
    for row in rows:
        selected = analyzed_selection(row)
        if selected is None:
            continue
        if (
            target_audio.track.language
            and selected.track.language
            and target_audio.track.language != selected.track.language
        ):
            continue
        try:
            caller.require_scan(facts[row.id], caller.media_decision(facts[row.id].media))
        except AccessDenied:
            continue
        matches.setdefault(selected.extraction_cache_key(), row)
    return next(iter(matches.values())) if len(matches) == 1 else None


def _transcript_models(source, target_scans):
    return tuple(
        dict.fromkeys([*(scan.audio_model for scan in target_scans), source.audio_model, None])
    )


def _transcript_cache_by_generation(source, audio, target_scans, digest):
    models = _transcript_models(source, target_scans)
    if target_transcript_ready(audio, models):
        return audio, models, True
    if digest:
        for scan in target_scans:
            analyzed = analyzed_selection(scan)
            if (
                analyzed
                and analyzed != audio
                and target_transcript_ready(analyzed, (scan.audio_model,))
            ):
                return analyzed, (scan.audio_model,), True
    return audio, models, False


def alignment_transcript_status(caller, source, target, audio, digest):
    """Distinguish a usable cache from one that needs its file identity rechecked."""
    target_scans = list(_target_audio_scans(caller, target, audio, digest))
    _, _, ready = _transcript_cache_by_generation(source, audio, target_scans, digest)
    if ready:
        return True, False
    if digest is None:
        for scan, prior, pinned in _visible_target_audio(caller, target, audio):
            if pinned and prior != audio and target_transcript_ready(prior, (scan.audio_model,)):
                return False, True
    return False, False


def align_from_transcript(
    caller, source, source_version, target, path, audio, duration, digest=None
):
    """Line up the cut and refine individual mutes with direct dialogue or audio evidence."""
    reference = _audio_reference(caller, source, source_version, audio)
    target_scans = list(_target_audio_scans(caller, target, audio, digest))
    if digest is None:
        cache_audio, models = audio, _transcript_models(source, target_scans)
    else:
        cache_audio, models, _ = _transcript_cache_by_generation(
            source, audio, target_scans, digest
        )
    try:
        target_words = (
            target_transcript_words(path, audio, models)
            if cache_audio == audio
            else target_transcript_words(path, audio, models, cache_audio)
        )
    except TranscriptAlignmentUnavailable as error:
        return (
            Alignment.from_anchors([]),
            [str(error) + ". Run an audio scan for this version, or use manual alignment."],
            None,
        )

    # A video-only scan can borrow landmarks from a visible scan of the same original cut.
    evidence = reference if reference is not None and not source.scan_audio else source
    recorded = saved_audio_cues(source_findings(caller.db, evidence.id))
    original_audio = analyzed_selection(reference) if reference is not None else None
    original_offset = (
        original_playback_offset(reference, original_audio, recorded) if original_audio else None
    )
    anchors, issues, evidence_id = [], [], None
    if original_offset is not None:
        try:
            anchors = match_audio_transcripts(
                reference,
                path,
                audio,
                models,
                target_words=target_words,
                original_offset=original_offset,
            )
        except TranscriptAlignmentUnavailable:
            pass
        else:
            evidence_id = reference.id

    target_scan = next(
        (
            scan
            for scan in target_scans
            if cache_audio != audio and analyzed_selection(scan) == cache_audio
        ),
        target_scans[0] if target_scans else None,
    )
    if not anchors:
        target_cues = (
            saved_audio_cues(source_findings(caller.db, target_scan.id)) if target_scan else []
        )
        anchors, method = cue_anchors(recorded, target_words, target_cues, duration)
        if method is None:
            return (
                Alignment.from_anchors([]),
                [
                    "Couldn’t align automatically with enough confidence. "
                    "Match two points on the original and target timelines manually."
                ],
                None,
            )
        if method == "dialogue":
            issues = [
                "Proposed from dialogue saved with the original scan. Check the target clips."
            ]
            evidence_id = evidence.id
        else:
            issues = [
                "Proposed from matching patterns in the flagged audio times. "
                "Check the target clips."
            ]
            evidence_id = target_scan.id

    alignment = Alignment.from_anchors(anchors)
    if not source.scan_audio:
        return alignment, issues, evidence_id
    matches = match_unmapped_dialogue(recorded, target_words, alignment)
    target_offset = (
        recorded_playback_offset(target_scan, cache_audio)
        if target_scan and original_audio
        else None
    )
    if original_offset is not None and target_offset is not None:
        matches.update(
            match_cached_audio_cues(
                recorded, alignment, original_audio, cache_audio, original_offset, target_offset
            )
        )
    return replace(alignment, audio_matches=matches), issues, evidence_id
