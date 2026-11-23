"""Durable file-bound requests and immutable actually analyzed audio snapshots."""

import json
import math
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from scanner.db import AudioTrackSelectionRow, ScanRow
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.persistence.rows.db_media_identity import MediaVersionRow, ScanMediaVersionRow
from scanner.pipeline.audio_tracks import AudioSelectionError, AudioTrack

from .repository_checkpoints import _checkpoint_state


def remembered_audio_selection(db: Session, path: Path) -> AudioSelection | None:
    row = db.get(AudioTrackSelectionRow, str(path))
    return AudioSelection.model_validate_json(row.selection_json) if row else None


def remember_audio_selection(db: Session, path: Path, selection: AudioSelection) -> None:
    row = db.get(AudioTrackSelectionRow, str(path))
    if row is None:
        row = AudioTrackSelectionRow(file_path=str(path))
        db.add(row)
    row.selection_json = selection.model_dump_json()
    db.commit()


def analyzed_track_matches_file(db: Session, path: Path, checksum: str, track: AudioTrack) -> bool:
    """Check for a completed scan of this stream on the exact file at this path."""
    selections = db.scalars(
        select(ScanRow.analyzed_audio_selection_json)
        .join(ScanMediaVersionRow, ScanMediaVersionRow.scan_id == ScanRow.id)
        .join(MediaVersionRow, MediaVersionRow.id == ScanMediaVersionRow.version_id)
        .where(
            ScanRow.resolved_local_path == str(path),
            ScanRow.origin == "generated",
            ScanRow.status == "complete",
            ScanRow.scan_audio.is_(True),
            ScanRow.analyzed_audio_selection_json.is_not(None),
            MediaVersionRow.content_sha256 == checksum,
        )
        .distinct()
    )
    for saved in selections:
        try:
            if AudioSelection.model_validate_json(saved).selection().track == track:
                return True
        except ValueError:
            continue
    return False


def recorded_playback_offset(scan, selection) -> float | None:
    """Read a worker-measured offset only for the recorded exact analyzed track."""
    timeline = _checkpoint_state(scan).get("analyzed_audio_timeline")
    if (
        scan.origin != "generated"
        or not isinstance(timeline, dict)
        or timeline.get("selection_key") != selection.extraction_cache_key()
    ):
        return None
    value = timeline.get("playback_start_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and abs(value) <= 172800 else None


def record_analyzed_audio_selection(
    db: Session,
    scan_id: str,
    selection: AudioSelection,
    *,
    playback_offset_seconds: float | None = None,
) -> None:
    """Claim actual input once before findings; retries may only reaffirm it."""
    encoded = selection.model_dump_json()
    row = db.get(ScanRow, scan_id)
    if row is None or row.requested_audio_selection_json != encoded:
        raise AudioSelectionError("Actual audio track does not match the queued request")
    previous = row.analyzed_audio_selection_json
    if previous is not None and previous != encoded:
        raise AudioSelectionError("Analyzed audio provenance cannot be changed")
    state = _checkpoint_state(row)
    changes = {}
    if playback_offset_seconds is not None:
        if not math.isfinite(playback_offset_seconds) or abs(playback_offset_seconds) > 172800:
            raise AudioSelectionError("The analyzed audio playback offset is invalid")
        timeline = {
            "selection_key": selection.selection().extraction_cache_key(),
            "playback_start_seconds": playback_offset_seconds,
        }
        prior = state.get("analyzed_audio_timeline")
        if prior is not None and prior != timeline:
            raise AudioSelectionError("Analyzed audio playback timing cannot be changed")
        if prior is None:
            state["analyzed_audio_timeline"] = timeline
            changes["checkpoint_state"] = json.dumps(state, sort_keys=True)
    if previous is None:
        changes["analyzed_audio_selection_json"] = encoded
    if not changes:
        return
    result = db.execute(
        update(ScanRow)
        .where(
            ScanRow.id == scan_id,
            ScanRow.requested_audio_selection_json == encoded,
            ScanRow.analyzed_audio_selection_json == previous,
            ScanRow.checkpoint_state == row.checkpoint_state,
        )
        .values(**changes)
    )
    if result.rowcount != 1:
        db.rollback()
        raise AudioSelectionError("Analyzed audio provenance changed concurrently")
    db.commit()
