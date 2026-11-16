"""Durable file-bound requests and immutable actually analyzed audio snapshots."""

from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from scanner.db import AudioTrackSelectionRow, ScanRow
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.pipeline.audio_tracks import AudioSelectionError


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


def record_analyzed_audio_selection(db: Session, scan_id: str, selection: AudioSelection) -> None:
    """Claim actual input once before findings; retries may only reaffirm it."""
    encoded = selection.model_dump_json()
    row = db.get(ScanRow, scan_id)
    if row is None or row.requested_audio_selection_json != encoded:
        raise AudioSelectionError("Actual audio track does not match the queued request")
    if row.analyzed_audio_selection_json is not None:
        if row.analyzed_audio_selection_json != encoded:
            raise AudioSelectionError("Analyzed audio provenance cannot be changed")
        return
    result = db.execute(
        update(ScanRow)
        .where(
            ScanRow.id == scan_id,
            ScanRow.requested_audio_selection_json == encoded,
            ScanRow.analyzed_audio_selection_json.is_(None),
        )
        .values(analyzed_audio_selection_json=encoded)
    )
    if result.rowcount != 1:
        db.rollback()
        raise AudioSelectionError("Analyzed audio provenance changed concurrently")
    db.commit()
