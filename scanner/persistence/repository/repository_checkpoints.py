"""Versioned scan checkpoints and resume admission."""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from scanner.db import ScanRow, ScanStageCoverageRow


def scan_checkpoint_stages(
    db: Session, scan_id: str, *, require_provenance: bool = False
) -> set[str]:
    """Return completed stages, rejecting unverified resume state when requested."""
    row = db.get(ScanRow, scan_id)
    if not row:
        return set()
    stages: set[str] = set()
    if row.visual_checkpoint_complete:
        stages.add("visual")
    if row.audio_extraction_checkpoint_complete:
        stages.add("audio_extract")
    if row.audio_checkpoint_complete:
        stages.add("audio_scan")
    stages.update(
        stage for stage, state in _checkpoint_state(row).items()
        if isinstance(state, dict) and state.get("completed") is True
    )
    if require_provenance and stages:
        checkpoint_state = _checkpoint_state(row)
        if checkpoint_state.get("format_version") != 1:
            raise ValueError("Unversioned scan checkpoint cannot safely resume; preserve the scan")
        for stage in stages:
            entry = checkpoint_state.get(stage)
            execution_id = entry.get("execution_id") if isinstance(entry, dict) else None
            if not isinstance(execution_id, str) or not execution_id:
                raise ValueError(f"Scan checkpoint for {stage} lacks execution provenance")
            coverage = db.get(ScanStageCoverageRow, (scan_id, stage, execution_id))
            if coverage is None or coverage.status != "complete":
                raise ValueError(f"Scan checkpoint for {stage} lacks completed stage coverage")
    return stages


def scan_checkpoint_cursor(db: Session, scan_id: str, stage: str) -> dict[str, object] | None:
    row = db.get(ScanRow, scan_id)
    if not row:
        return None
    state = _checkpoint_state(row).get(stage)
    if not isinstance(state, dict):
        return None
    cursor = state.get("cursor")
    return cursor if isinstance(cursor, dict) else None


def mark_scan_checkpoint(
    db: Session,
    scan_id: str,
    stage: str,
    *,
    completed: bool = True,
    cursor: dict[str, object] | None = None,
    execution_id: str | None = None,
) -> None:
    """Commit stage completion and its matching provenance in one transaction."""
    row = db.get(ScanRow, scan_id)
    if not row:
        return
    if stage == "visual" and completed:
        row.visual_checkpoint_complete = True
    elif stage == "audio_extract" and completed:
        row.audio_extraction_checkpoint_complete = True
    elif stage == "audio_scan" and completed:
        row.audio_checkpoint_complete = True
    checkpoint_state = _checkpoint_state(row)
    if execution_id is not None:
        coverage = db.get(ScanStageCoverageRow, (scan_id, stage, execution_id))
        if coverage is None:
            raise ValueError("Cannot complete a stage without recorded provenance")
        if completed:
            coverage.status = "complete"
        checkpoint_state["format_version"] = 1
    checkpoint_state[stage] = {
        "completed": completed,
        "cursor": cursor or {},
        **({"execution_id": execution_id} if execution_id is not None else {}),
    }
    row.checkpoint_state = json.dumps(checkpoint_state, sort_keys=True)
    row.checkpoint_stage = f"{stage}_{'complete' if completed else 'in_progress'}"
    row.checkpoint_updated_at = datetime.now(timezone.utc)
    row.updated_at = row.checkpoint_updated_at
    db.commit()


def _checkpoint_state(row: ScanRow) -> dict[str, object]:
    try:
        value = json.loads(row.checkpoint_state or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def commit_scan_stage_results(
    db: Session, scan_id: str, execution_id: str, stage: str, findings
) -> None:
    """Publish findings and their completion marker atomically for safe retries."""
    from .repository_catalog import create_scan_findings_batch

    create_scan_findings_batch(db, scan_id, findings, commit=False)
    mark_scan_checkpoint(db, scan_id, stage, execution_id=execution_id)
