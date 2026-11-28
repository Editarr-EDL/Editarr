"""The durable stage marker cannot be published ahead of its findings or coverage."""

import json

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from scanner import repository
from scanner.db import ScanStageCoverageRow
from scanner.models import FindingCreate
from scanner.runtime.worker_stage import commit_worker_stage_results, record_worker_stage
from tests.queue_support import _enqueue_scan, _queue_factory


def _finding(scan_id: str) -> FindingCreate:
    return FindingCreate(
        video_id=scan_id, category="violence", subcategory="violence", start_seconds=3,
        end_seconds=3.5, severity="medium", confidence=1.0, source="visual",
    )


@pytest.mark.parametrize(
    ("visual_settings", "expected_intervals", "expected_sampling"),
    [
        ({}, [{"start_seconds": 0.0, "end_seconds": 90}], None),
        ({"cached_scene_findings_reused": True}, None, None),
        ({"coarse_scan_enabled": True, "coarse_scan_interval_seconds": 4}, None, 4),
    ],
)
def test_stage_commit_exposes_final_coverage_with_checkpoint_and_findings(
    visual_settings, expected_intervals, expected_sampling,
):
    factory = _queue_factory()
    with factory() as db:
        scan, _queue_item = _enqueue_scan(db, "stage-commit")
        record_worker_stage(
            db, scan.id, "worker-token", "visual", "started",
            {"video_model": "nudenet", "duration_seconds": 20},
        )
        commits = []
        event.listen(db, "after_commit", lambda _session: commits.append(True))
        commit_worker_stage_results(
            db, scan.id, "worker-token", "visual", [_finding(scan.id)],
            effective={"duration_seconds": 90, **visual_settings},
        )
        assert commits == [True]

    with factory() as db:
        coverage = db.get(ScanStageCoverageRow, (scan.id, "visual", "worker-token"))
        assert coverage.status == "complete" and coverage.model_id == "nudenet"
        assert json.loads(coverage.effective_settings_json) == {
            "video_model": "nudenet", "duration_seconds": 90, **visual_settings,
        }
        intervals = json.loads(coverage.intervals_json) if coverage.intervals_json else None
        assert intervals == expected_intervals
        assert coverage.sampling_seconds == expected_sampling
        assert repository.scan_checkpoint_stages(db, scan.id, require_provenance=True) == {"visual"}
        assert len(repository.get_scan_findings(db, scan.id).findings) == 1


def test_coverage_write_failure_rolls_back_findings_and_checkpoint():
    factory = _queue_factory()
    with factory() as db:
        scan, _queue_item = _enqueue_scan(db, "stage-rollback")
        effective = {"video_model": "nudenet", "duration_seconds": 90}
        record_worker_stage(db, scan.id, "worker-token", "visual", "started", effective)
        with pytest.raises(IntegrityError, match="ck_scan_stage_coverage_sampling"):
            commit_worker_stage_results(
                db, scan.id, "worker-token", "visual", [_finding(scan.id)],
                effective={**effective, "coarse_scan_enabled": True,
                           "coarse_scan_interval_seconds": 0},
            )
        assert repository.scan_checkpoint_stages(db, scan.id, require_provenance=True) == set()

    with factory() as db:
        coverage = db.get(ScanStageCoverageRow, (scan.id, "visual", "worker-token"))
        assert coverage.status == "unknown"
        assert json.loads(coverage.effective_settings_json) == effective
        assert not repository.get_scan_findings(db, scan.id).findings
