"""Attempt-specific scanner execution provenance."""

import json
from collections.abc import Iterable

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from scanner.db import ScanRow, ScanStageCoverageRow
from scanner.settings.version_info import version_information

from .repository_checkpoints import _checkpoint_state


def _completed_execution(scan: ScanRow, stage: str) -> str | None:
    checkpoint = _checkpoint_state(scan)
    entry = checkpoint.get(stage)
    if (
        checkpoint.get("format_version") != 1
        or not isinstance(entry, dict)
        or entry.get("completed") is not True
        or not isinstance(execution_id := entry.get("execution_id"), str)
    ):
        return None
    return execution_id


def _decode_settings(payload: str | None) -> dict[str, object] | None:
    try:
        settings = json.loads(payload or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    return settings if isinstance(settings, dict) else None


def completed_stage_settings(scan: ScanRow, stage: str) -> dict[str, object] | None:
    """Read settings from the completed execution selected by the scan checkpoint."""
    execution_id = _completed_execution(scan, stage)
    if execution_id is None:
        return None
    for row in scan.stage_coverage:
        if row.stage == stage and row.execution_id == execution_id and row.status == "complete":
            return _decode_settings(row.effective_settings_json)
    return None


def completed_stage_settings_by_scan(
    db: Session, scans: Iterable[ScanRow], stage: str
) -> dict[str, dict[str, object]]:
    """Fetch the exact checkpoint-selected settings for a bounded set of scans."""
    keys = [
        (scan.id, execution_id)
        for scan in scans
        if (execution_id := _completed_execution(scan, stage)) is not None
    ]
    if not keys:
        return {}
    rows = db.execute(
        select(ScanStageCoverageRow.scan_id, ScanStageCoverageRow.effective_settings_json).where(
            ScanStageCoverageRow.stage == stage,
            ScanStageCoverageRow.status == "complete",
            tuple_(ScanStageCoverageRow.scan_id, ScanStageCoverageRow.execution_id).in_(keys),
        )
    )
    return {
        scan_id: settings
        for scan_id, payload in rows
        if (settings := _decode_settings(payload)) is not None
    }


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
