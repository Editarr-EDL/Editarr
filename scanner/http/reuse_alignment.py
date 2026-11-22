"""Read authorized audio evidence for shared scan and import verification."""

from scanner.db import MediaItemRow, ScanRow
from scanner.modeling.cue_alignment import cue_anchors
from scanner.persistence.repository.scan_jobs import scan_job_facts
from scanner.persistence.repository.version_reuse import source_findings
from scanner.persistence.rows.db_media_identity import ScanMediaVersionRow
from scanner.runtime.version_reuse_audio import (
    TranscriptAlignmentUnavailable,
    analyzed_selection,
    match_audio_transcripts,
    saved_audio_cues,
    target_transcript_ready,
    target_transcript_words,
)
from scanner.security.access.decisions import AccessDenied


def _target_audio_scans(caller, target, selection):
    """Find visible generated audio findings on this same file generation and track."""
    db = caller.db
    candidates = (
        db.query(ScanRow)
        .join(MediaItemRow, MediaItemRow.id == ScanRow.media_item_id)
        .filter(
            MediaItemRow.server_id == target.source.server_id,
            MediaItemRow.jellyfin_id == target.source.item_id,
            ScanRow.media_source_id == target.source.media_source_id,
            ScanRow.status == "complete",
            ScanRow.origin == "generated",
            ScanRow.scan_audio.is_(True),
            ScanRow.analyzed_audio_selection_json.is_not(None),
        )
        .order_by(ScanRow.created_at.desc(), ScanRow.id)
        .limit(100)
    )
    matches = [scan for scan in candidates if analyzed_selection(scan) == selection]
    facts = scan_job_facts(db, [scan.id for scan in matches])
    for scan in matches:
        try:
            caller.require_scan(facts[scan.id], caller.media_decision(facts[scan.id].media))
        except AccessDenied:
            continue
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
    return tuple(dict.fromkeys([*(scan.audio_model for scan in target_scans), source.audio_model, None]))


def alignment_transcript_ready(caller, source, target, audio):
    """Check the same selected-track cache that automatic alignment would read."""
    return target_transcript_ready(audio, _transcript_models(source, _target_audio_scans(caller, target, audio)))


def align_from_transcript(caller, source, source_version, target, path, audio, duration):
    """Compare full transcripts when available, or match saved cue evidence to target audio."""
    reference = _audio_reference(caller, source, source_version, audio)
    target_scans = list(_target_audio_scans(caller, target, audio))
    models = _transcript_models(source, target_scans)
    try:
        target_words = target_transcript_words(path, audio, models)
    except TranscriptAlignmentUnavailable as error:
        return (
            [],
            [str(error) + ". Run an audio scan for this version, or use manual alignment."],
            None,
        )

    if reference is not None:
        try:
            anchors = match_audio_transcripts(
                reference,
                path,
                audio,
                models,
                target_words=target_words,
            )
        except TranscriptAlignmentUnavailable:
            pass
        else:
            return anchors, [], reference.id

    # Prefer the attached scan. For video-only evidence, an authorized scan of
    # the original version may be the only source of dialogue landmarks.
    evidence = reference if reference is not None and not source.scan_audio else source
    recorded = saved_audio_cues(source_findings(caller.db, evidence.id))
    target_scan = target_scans[0] if target_scans else None
    target_cues = (
        saved_audio_cues(source_findings(caller.db, target_scan.id)) if target_scan else []
    )
    anchors, method = cue_anchors(recorded, target_words, target_cues, duration)
    if method is None:
        return (
            [],
            [
                "Couldn’t align automatically with enough confidence. "
                "Match two points on the original and target timelines manually."
            ],
            None,
        )
    if method == "dialogue":
        return (
            anchors,
            ["Proposed from dialogue saved with the original scan. Check the target clips."],
            evidence.id,
        )
    return (
        anchors,
        ["Proposed from matching patterns in the flagged audio times. Check the target clips."],
        target_scan.id,
    )
