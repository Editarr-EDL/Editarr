"""Project one scan worker stage into persisted evidence coverage."""

from sqlalchemy.orm import Session

from scanner.models import FindingCreate
from scanner.persistence.repository.repository_checkpoints import commit_scan_stage_results
from scanner.persistence.repository.repository_provenance import record_scan_stage
from scanner.pipeline.provenance import stage_model_identity


def _analyzed_intervals(
    stage: str, effective: dict[str, object]
) -> list[dict[str, float]] | None:
    duration = effective.get("duration_seconds")
    if not isinstance(duration, (int, float)):
        return None
    if stage == "credits_detection":
        tail_window = effective.get("tail_window_seconds")
        if isinstance(tail_window, (int, float)) and tail_window > 0:
            return [{"start_seconds": max(0.0, duration - tail_window), "end_seconds": duration}]
    elif stage in {"visual", "audio_extract", "audio_scan"} and duration > 0:
        if stage == "visual" and (
            effective.get("coarse_scan_enabled") or effective.get("cached_scene_findings_reused")
        ):
            return None
        return [{"start_seconds": 0.0, "end_seconds": duration}]
    return None


def _sampling_seconds(stage: str, effective: dict[str, object]) -> float | None:
    if stage == "visual" and effective.get("coarse_scan_enabled"):
        interval = effective.get("coarse_scan_interval_seconds")
    elif stage == "credits_detection":
        interval = effective.get("frame_sample_interval_seconds")
    else:
        return None
    return interval if isinstance(interval, (int, float)) else None


def record_worker_stage(db, scan_id, execution_id, stage, state, effective):
    """Persist stage outcomes that do not jointly publish scan findings."""
    model = stage_model_identity(stage, effective) if state not in {"reused", "no_inputs"} else {}
    record_scan_stage(
        db, scan_id, stage, execution_id, state, effective,
        **model,
        intervals=_analyzed_intervals(stage, effective) if state == "complete" else None,
        sampling_seconds=_sampling_seconds(stage, effective),
    )


def commit_worker_stage_results(
    db: Session,
    scan_id: str,
    execution_id: str,
    stage: str,
    findings: list[FindingCreate],
    *,
    effective: dict[str, object],
) -> None:
    """Publish the authoritative stage result under the caller's worker gate."""
    commit_scan_stage_results(
        db, scan_id, execution_id, stage, findings,
        effective=effective,
        intervals=_analyzed_intervals(stage, effective),
        sampling_seconds=_sampling_seconds(stage, effective),
    )
