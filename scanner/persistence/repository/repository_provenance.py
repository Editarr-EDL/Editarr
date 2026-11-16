"""Attempt-specific scanner execution provenance."""

import json

from sqlalchemy.orm import Session

from scanner.db import ScanRow, ScanStageCoverageRow
from scanner.settings.version_info import version_information


def record_scan_stage(
    db: Session,
    scan_id: str,
    stage: str,
    execution_id: str,
    state: str,
    settings: dict[str, object],
    *,
    model_id: str | None = None,
    model_revision: str | None = None,
    model_digest: str | None = None,
    quantization: str | None = None,
    intervals: list[dict[str, float]] | None = None,
    sampling_seconds: float | None = None,
) -> None:
    """Persist a real stage attempt without replacing prior worker attempts."""
    scan = db.get(ScanRow, scan_id)
    if scan is None:
        raise ValueError("Scan is unavailable for stage provenance")
    key = (scan_id, stage, execution_id)
    row = db.get(ScanStageCoverageRow, key)
    if row is None:
        if state not in {"started", "unsupported", "requested", "reused", "no_inputs"}:
            raise ValueError("Stage completion requires a recorded attempt")
        metadata = version_information()
        for previous in scan.stage_coverage:
            if (
                previous.stage == stage and previous.status == "unknown"
                and '"skip_reason":"no_inputs"' not in (previous.effective_settings_json or "")
            ):
                previous.status = "partial"
        row = ScanStageCoverageRow(
            scan_id=scan_id,
            stage=stage,
            execution_id=execution_id,
                status=(
                    "unknown" if state in {"started", "requested"} else
                    "partial" if state == "reused" else
                    "disabled" if state == "no_inputs" else "unsupported"
                ),
            source=scan.origin,
            release_version=metadata.release,
            scanner_behavior_version=metadata.scanner_behavior,
            build_revision=metadata.build_revision,
            model_id=model_id,
            model_revision=model_revision,
            model_digest=model_digest,
            quantization=quantization,
            effective_settings_json=json.dumps(
                {**settings, "skip_reason": "cached_result" if state == "reused" else "no_inputs"}
                if state in {"reused", "no_inputs"} else settings,
                sort_keys=True, separators=(",", ":"),
            ),
            intervals_json=(
                json.dumps(intervals, sort_keys=True, separators=(",", ":"))
                if intervals is not None else None
            ),
            sampling_seconds=sampling_seconds,
        )
        db.add(row)
    elif state == "no_inputs":
        row.status = "disabled"
        row.model_id = None
        row.model_revision = None
        row.model_digest = None
        row.quantization = None
        previous_settings = json.loads(row.effective_settings_json or "{}")
        previous_settings["skip_reason"] = "no_inputs"
        row.effective_settings_json = json.dumps(
            previous_settings, sort_keys=True, separators=(",", ":")
        )
    elif state in {"partial", "failed", "complete", "unsupported"}:
        row.status = state
        if intervals is not None:
            row.intervals_json = json.dumps(
                intervals, sort_keys=True, separators=(",", ":")
            )
    db.commit()
