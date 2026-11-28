"""Visual coverage distinguishes a resumed scan from generic cached findings."""

import json
from functools import partial
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.db import Base, ScanStageCoverageRow
from scanner.formats.community.artifact_v3 import encode_artifact_v3
from scanner.integrations.community.submission_projection import project_submission
from scanner.pipeline import runner, runner_scenes
from scanner.pipeline.scenes import Scene
from scanner.pipeline.visual_checkpoints import save_scene_checkpoint, scene_checkpoint_path
from scanner.runtime.worker_stage import commit_worker_stage_results, record_worker_stage
from tests.test_community_submission_projection import VIDEO_CATEGORIES, options, scan_data
from tests.test_execution_provenance import _scan


@pytest.mark.parametrize(
    ("private_checkpoint", "coarse", "covered"),
    [(True, False, True), (True, True, False), (False, False, False)],
    ids=["resumed-fine-scan", "resumed-coarse-scan", "generic-cache"],
)
def test_reused_scene_results_have_only_verified_coverage(
    tmp_path, monkeypatch, private_checkpoint, coarse, covered,
):
    video = tmp_path / "synthetic.mkv"
    video.touch()
    scenes = [
        Scene(start_seconds=0, end_seconds=10, index=0),
        Scene(start_seconds=10, end_seconds=20, index=1),
    ]
    analyzed = []

    def scan_visual(path, scan_id, work_dir, *, checkpoint_dir=None, video_hash, **settings):
        assert (checkpoint_dir is not None) == private_checkpoint
        if checkpoint_dir is not None:
            # A previous attempt published this completed scene for the same job.
            cached_result = scene_checkpoint_path(checkpoint_dir, scenes[0], phase="complete")
        else:
            cached_result = work_dir / "visual-results" / "scene_0000.json"
            cached_result.parent.mkdir()
        save_scene_checkpoint(cached_result, [])
        return runner._scan_visual_by_scene(
            path, scan_id, work_dir, cached_scenes=scenes, checkpoint_dir=checkpoint_dir, **settings
        )

    def sample_frames(_path, scene, _directory, *, interval_seconds, **_options):
        if interval_seconds is None:
            analyzed.append(scene.index)
        return [(tmp_path / "frame.jpg", scene.start_seconds)]

    def summarize(findings, _frames, *, provenance_callback, **_options):
        provenance_callback("no_inputs")
        return findings

    monkeypatch.setattr(runner.settings, "data_dir", tmp_path)
    monkeypatch.setattr(runner.settings, "frames_dir", tmp_path / "frames")
    monkeypatch.setattr(runner, "require_available", lambda *_: None)
    monkeypatch.setattr(runner, "qwen_enrichment_capability", lambda: SimpleNamespace(
        availability="unavailable", enrichment_behavior="detector_findings_only",
    ))
    monkeypatch.setattr(runner_scenes, "defer_descriptions", lambda *_: False)
    monkeypatch.setattr(runner, "sample_scene_frames", sample_frames)
    monkeypatch.setattr(runner, "get_video_frame_interval", lambda *_: 1.0)
    monkeypatch.setattr(runner, "scan_frame", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "scan_frames", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "summarize_flagged_segments", summarize)
    monkeypatch.setattr(runner, "_scan_visual_isolated", scan_visual)

    engine = create_engine(f"sqlite:///{tmp_path / 'coverage.db'}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            scan_id = _scan(db, video).id
            execution = "current-attempt"

            def publish(stage, findings, effective):
                commit_worker_stage_results(
                    db, scan_id, execution, stage, findings, effective=effective
                )

            runner.run_scan(
                db, str(video), scan_video=True, video_model="nudenet", scan_audio=False,
                video_id=scan_id, video_duration_seconds=20, media_cache_key="same-media",
                coarse_scan_enabled=coarse, coarse_scan_interval_seconds=5.0 if coarse else None,
                status_updater=lambda *_: None, findings_writer=lambda *_: None,
                stage_callback=partial(record_worker_stage, db, scan_id, execution),
                stage_results_writer=publish if private_checkpoint else None,
            )
            assert analyzed == [1]
            reused = db.get(ScanStageCoverageRow, (scan_id, "visual_qwen:scene_0000", execution))
            assert reused.status == "partial"
            assert reused.model_id is None
            visual = db.get(ScanStageCoverageRow, (scan_id, "visual", execution))
            assert visual.status == "complete"
            effective = json.loads(visual.effective_settings_json)
            assert effective.get("checkpoint_scene_findings_reused", False) is private_checkpoint
            assert effective.get("cached_scene_findings_reused", False) is not private_checkpoint
            stages = [{
                "stage": visual.stage,
                "status": visual.status,
                "intervals_json": visual.intervals_json,
            }]
            projection = project_submission(
                scan_data(stages=stages), "scan", options(duration_ms=20_000), None
            )
            coverage = encode_artifact_v3(projection.artifact).artifact.coverage
            if covered:
                assert {value.category for value in coverage} == set(VIDEO_CATEGORIES)
                assert all(
                    [(span.start_ms, span.end_ms) for span in value.intervals] == [(0, 20_000)]
                    for value in coverage
                )
            else:
                assert not coverage
    finally:
        engine.dispose()
