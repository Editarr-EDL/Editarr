"""Read authorized audio evidence for shared scan and import verification."""

from dataclasses import replace

from sqlalchemy import and_, or_

from scanner.db import MediaItemRow, ScanRow
from scanner.modeling.cue_alignment import cue_anchors, match_unmapped_dialogue
from scanner.modeling.model_version_reuse import ReuseAnchor
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


def _visible_target_audio(caller, target, selection, version_id=None):
    """Find authorized recordings of the selected stream at known file locations."""
    db = caller.db
    locations = [
        and_(
            MediaItemRow.jellyfin_id == target.source.item_id,
            ScanRow.media_source_id == target.source.media_source_id,
        ),
        ScanRow.resolved_local_path == target.media.resolved_path,
    ]
    if version_id:
        locations.append(MediaVersionRow.id == version_id)
    candidates = (
        db.query(ScanRow, MediaVersionRow)
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
    for scan, version in candidates:
        analyzed = analyzed_selection(scan)
        if analyzed and analyzed.track == selection.track:
            matches.append((scan, analyzed, version))
    facts = scan_job_facts(db, [scan.id for scan, _, _ in matches])
    for scan, analyzed, version in matches:
        try:
            caller.require_scan(facts[scan.id], caller.media_decision(facts[scan.id].media))
        except AccessDenied:
            continue
        yield scan, analyzed, version


def _target_audio_scans(caller, target, selection, version_id=None):
    """Require the same selected local generation or a verified Editarr version."""
    for scan, analyzed, pinned in _visible_target_audio(caller, target, selection, version_id):
        if analyzed == selection or (pinned is not None and pinned.id == version_id):
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


def _transcript_cache_by_generation(source, audio, target_scans, version_id):
    models = _transcript_models(source, target_scans)
    if target_transcript_ready(audio, models):
        return audio, models, True
    if version_id:
        for scan in target_scans:
            analyzed = analyzed_selection(scan)
            if (
                analyzed
                and analyzed != audio
                and target_transcript_ready(analyzed, (scan.audio_model,))
            ):
                return analyzed, (scan.audio_model,), True
    return audio, models, False


def alignment_transcript_status(
    caller, source, target, audio, version_id, *, sampled=False, known_content_sha256=None
):
    """Distinguish a usable cache from one that needs its file identity rechecked."""
    target_scans = list(_target_audio_scans(caller, target, audio, version_id))
    _, _, ready = _transcript_cache_by_generation(source, audio, target_scans, version_id)
    if ready:
        return True, False
    if version_id is None and not sampled:
        for scan, prior, pinned in _visible_target_audio(caller, target, audio):
            if (
                pinned is not None
                and (
                    pinned.sampled_md5
                    or (known_content_sha256 and known_content_sha256 == pinned.content_sha256)
                )
                and prior != audio
                and target_transcript_ready(prior, (scan.audio_model,))
            ):
                return False, True
    return False, False


def align_from_transcript(
    caller, source, source_version, target, path, audio, duration, version_id=None
):
    """Line up the cut and refine individual mutes with direct dialogue or audio evidence."""
    reference = _audio_reference(caller, source, source_version, audio)
    target_scans = list(_target_audio_scans(caller, target, audio, version_id))
    if version_id is None:
        cache_audio, models = audio, _transcript_models(source, target_scans)
    else:
        cache_audio, models, _ = _transcript_cache_by_generation(
            source, audio, target_scans, version_id
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


def _identity_anchors(source_start, source_end):
    if source_end <= source_start:
        return []
    return [
        ReuseAnchor(source_seconds=point, target_seconds=point)
        for point in (source_start, source_end)
    ]


def align_reuse(
    caller,
    request,
    source,
    source_version,
    target,
    version_id,
    path,
    duration,
    audio,
    *,
    allow_exact,
):
    include_audio = "audio" in request.channels
    same_content = allow_exact and source.origin == "generated" and source_version.id == version_id
    original_audio = analyzed_selection(source) if include_audio else None
    same_track = not include_audio or (
        original_audio is not None and original_audio.track == audio.track
    )
    if same_content and same_track:
        return Alignment.from_anchors(_identity_anchors(0, duration)), True, [], None
    if request.method == "transcript":
        alignment, issues, evidence_id = align_from_transcript(
            caller, source, source_version, target, path, audio, duration, version_id
        )
        return alignment, False, issues, evidence_id
    if request.method == "original":
        document = source.mcf_document
        start = document.material_start_seconds if document else 0
        end = document.material_end_seconds if document else source.duration_seconds
        limit = min(duration, end) if end is not None else duration
        return (
            Alignment.from_anchors(_identity_anchors(start, limit)),
            False,
            [
                "Check cue times on the target before approving the original timestamps",
            ],
            None,
        )
    if not request.anchors:
        return (
            Alignment.from_anchors([]),
            False,
            ["Add matched timing points and preview the cues on this version"],
            None,
        )
    if any(anchor.target_seconds > duration for anchor in request.anchors):
        raise ValueError("Target timing points must be within the target playback duration")
    if source.origin == "generated" and source.duration_seconds is not None:
        if any(anchor.source_seconds > source.duration_seconds for anchor in request.anchors):
            raise ValueError("Original timing points must be within the original playback duration")
    return Alignment.from_anchors(request.anchors), False, [], None
