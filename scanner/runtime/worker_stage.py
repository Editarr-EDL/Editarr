"""Project one scan worker stage into persisted evidence coverage."""

from scanner.persistence.repository.repository_provenance import record_scan_stage
from scanner.pipeline.provenance import stage_model_identity


def record_worker_stage(db, scan_id, execution_id, stage, state, effective):
    duration_seconds = effective.get("duration_seconds")
    tail_window = effective.get("tail_window_seconds")
    if stage == "credits_detection" and isinstance(duration_seconds, (int, float)):
        intervals = (
            [
                {
                    "start_seconds": max(0.0, duration_seconds - tail_window),
                    "end_seconds": duration_seconds,
                }
            ]
            if isinstance(tail_window, (int, float)) and tail_window > 0
            else None
        )
    elif stage == "credits_cache":
        intervals = None
    else:
        intervals = (
            [{"start_seconds": 0.0, "end_seconds": duration_seconds}]
            if state == "complete"
            and stage in {"visual", "audio_extract", "audio_scan"}
            and isinstance(duration_seconds, (int, float))
            and duration_seconds > 0
            and not (
                stage == "visual"
                and (
                    effective.get("coarse_scan_enabled")
                    or effective.get("cached_scene_findings_reused")
                )
            )
            else None
        )
    record_scan_stage(
        db,
        scan_id,
        stage,
        execution_id,
        state,
        effective,
        **(
            stage_model_identity(stage, effective)
            if state not in {"reused", "no_inputs"}
            else {
                "model_id": None,
                "model_revision": None,
                "model_digest": None,
                "quantization": None,
            }
        ),
        intervals=intervals if state == "complete" else None,
        sampling_seconds=(
            effective.get("coarse_scan_interval_seconds")
            if stage == "visual" and effective.get("coarse_scan_enabled")
            else effective.get("frame_sample_interval_seconds")
            if stage == "credits_detection"
            else None
        ),
    )
