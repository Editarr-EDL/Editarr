"""Stage provenance is execution evidence, not queued configuration."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository, worker
from scanner.db import Base, ScanRow, ScanStageCoverageRow
from scanner.persistence.repository import repository_provenance
from scanner.pipeline import runner
from scanner.pipeline.provenance import stage_model_identity
from scanner.pipeline.scenes import Scene
from scanner.settings.capabilities import CapabilityUnavailableError


def _scan(db, path):
    media = repository.upsert_media_item(
        db, server_id="fixture", jellyfin_id="fixture-movie", library_id=None,
        item_type="Movie", name="Synthetic", path=str(path),
    )
    return repository.create_scan(
        db, media_item=media, resolved_local_path=str(path), duration_seconds=90.0,
        scan_audio=True, audio_model="whisper", audio_filter="better_profanity",
        scan_video=True, video_model="nudenet",
    )


def test_runtime_selection_and_mixed_resume_keep_distinct_stage_versions(tmp_path, monkeypatch):
    path = tmp_path / "fixture.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            scan = _scan(db, tmp_path / "synthetic.mkv")
            selected = {"video_model": "image_safety_gate", "duration_seconds": 90.0}
            monkeypatch.setattr(
                repository_provenance, "version_information",
                lambda: SimpleNamespace(
                    release="0.1.0", scanner_behavior="scanner-1", build_revision=None,
                ),
            )
            repository.record_scan_stage(
                db, scan.id, "visual", "worker-a", "started", selected,
                **stage_model_identity("visual", selected),
            )
            repository.mark_scan_checkpoint(db, scan.id, "visual", execution_id="worker-a")
            assert repository.scan_checkpoint_stages(
                db, scan.id, require_provenance=True
            ) == {"visual"}
            first = db.get(ScanStageCoverageRow, (scan.id, "visual", "worker-a"))
            assert db.get(ScanRow, scan.id).video_model == "nudenet"
            assert first.model_id == "image_safety_gate"
            assert first.scanner_behavior_version == "scanner-1"
            assert first.status == "complete"

            monkeypatch.setattr(
                repository_provenance, "version_information",
                lambda: SimpleNamespace(
                    release="0.1.1", scanner_behavior="scanner-2", build_revision=None,
                ),
            )
            audio = {"audio_model": "mlx_whisper", "audio_filter": "better_profanity"}
            repository.record_scan_stage(
                db, scan.id, "audio_scan", "worker-b", "started", audio,
                **stage_model_identity("audio_scan", audio),
            )
            repository.mark_scan_checkpoint(db, scan.id, "audio_scan", execution_id="worker-b")
            assert repository.scan_checkpoint_stages(
                db, scan.id, require_provenance=True
            ) == {"visual", "audio_scan"}
            assert first.scanner_behavior_version == "scanner-1"
            second = db.get(ScanStageCoverageRow, (scan.id, "audio_scan", "worker-b"))
            assert second.model_id == "mlx_whisper"
            assert second.scanner_behavior_version == "scanner-2"
            assert second.status == "complete"
    finally:
        engine.dispose()


def test_unversioned_or_incomplete_checkpoint_cannot_skip_stage(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fixture.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            scan = _scan(db, tmp_path / "synthetic.mkv")
            repository.mark_scan_checkpoint(db, scan.id, "visual")
            with pytest.raises(ValueError, match="Unversioned scan checkpoint"):
                repository.scan_checkpoint_stages(db, scan.id, require_provenance=True)
            assert db.get(ScanRow, scan.id).visual_checkpoint_complete

            db.get(ScanRow, scan.id).visual_checkpoint_complete = False
            db.get(ScanRow, scan.id).checkpoint_state = "{}"
            db.commit()
            repository.record_scan_stage(
                db, scan.id, "visual", "worker-a", "started", {"video_model": "nudenet"},
            )
            repository.mark_scan_checkpoint(db, scan.id, "visual", execution_id="worker-a")
            db.get(ScanStageCoverageRow, (scan.id, "visual", "worker-a")).status = "partial"
            db.commit()
            with pytest.raises(ValueError, match="lacks completed stage coverage"):
                repository.scan_checkpoint_stages(db, scan.id, require_provenance=True)
    finally:
        engine.dispose()


def test_cached_scene_records_reuse_without_qwen_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "get_video_frame_interval", lambda *_args: 1.0)
    monkeypatch.setattr(runner, "_load_cached_visual_findings", lambda _path: [])
    monkeypatch.setattr(
        runner, "scan_frames", lambda *_args, **_kwargs: pytest.fail("ran inference")
    )
    events = []
    assert runner._scan_visual_by_scene(
        "synthetic.mkv", "scan", tmp_path, False, None,
        cached_scenes=[Scene(start_seconds=0, end_seconds=10, index=1)],
        provenance_callback=events.append,
    ) == []
    assert events == ["scene_0001:reused"]

    engine = create_engine(f"sqlite:///{tmp_path / 'fixture.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            scan = _scan(db, tmp_path / "synthetic.mkv")
            worker._record_stage(
                db, scan.id, "worker-a", "visual_qwen:scene_0001", "reused",
                {"qwen_model_requested": "qwen3-vl-4b-instruct-4bit"},
            )
            row = db.get(ScanStageCoverageRow, (scan.id, "visual_qwen:scene_0001", "worker-a"))
            assert row.status == "partial"
            assert json.loads(row.effective_settings_json)["skip_reason"] == "cached_result"
            assert row.model_id is None
            assert row.intervals_json is None
            worker._record_stage(
                db, scan.id, "worker-a", "visual", "started",
                {"video_model": "nudenet", "duration_seconds": 10},
            )
            worker._record_stage(
                db, scan.id, "worker-a", "visual", "complete",
                {"video_model": "nudenet", "duration_seconds": 10,
                 "cached_scene_findings_reused": True},
            )
            assert db.get(ScanStageCoverageRow, (scan.id, "visual", "worker-a"))\
                .intervals_json is None
    finally:
        engine.dispose()


def test_visual_scene_relays_observed_qwen_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "get_video_frame_interval", lambda *_args: 1.0)
    monkeypatch.setattr(runner, "_load_cached_visual_findings", lambda _path: None)
    monkeypatch.setattr(runner, "sample_scene_frames", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "scan_frames", lambda *_args, **_kwargs: [])

    def summarize(_findings, _frames, *, provenance_callback, **_kwargs):
        provenance_callback("no_inputs")
        return []

    monkeypatch.setattr(runner, "summarize_flagged_segments", summarize)
    events = []
    assert runner._scan_visual_by_scene(
        "synthetic.mkv", "scan", tmp_path, False, None,
        cached_scenes=[Scene(start_seconds=0, end_seconds=10, index=1)],
        provenance_callback=events.append,
    ) == []
    assert events == ["scene_0001:no_inputs"]

    engine = create_engine(f"sqlite:///{tmp_path / 'fixture.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            scan = _scan(db, tmp_path / "synthetic.mkv")
            worker._record_stage(
                db, scan.id, "worker-a", "visual_qwen:scene_0001", "no_inputs",
                {"qwen_model_requested": "qwen3-vl-4b-instruct-4bit"},
            )
            row = db.get(ScanStageCoverageRow, (scan.id, "visual_qwen:scene_0001", "worker-a"))
            assert row.status == "disabled"
            assert json.loads(row.effective_settings_json)["skip_reason"] == "no_inputs"
            assert row.model_id is None
    finally:
        engine.dispose()


def test_failed_enrichment_and_interrupted_stage_do_not_complete_checkpoint(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fixture.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            scan = _scan(db, tmp_path / "synthetic.mkv")
            repository.record_scan_stage(
                db, scan.id, "visual_qwen:scene_0001", "worker-a", "started",
                {"qwen_model_requested": "qwen3-vl-4b-instruct-4bit"},
            )
            repository.record_scan_stage(
                db, scan.id, "visual_qwen:scene_0001", "worker-a", "failed", {},
            )
            repository.record_scan_stage(
                db, scan.id, "visual", "worker-a", "started", {"video_model": "nudenet"},
            )
            repository.record_scan_stage(
                db, scan.id, "visual", "worker-a", "partial", {},
            )
            assert db.get(ScanStageCoverageRow, (scan.id, "visual_qwen:scene_0001", "worker-a"))\
                .status == "failed"
            assert db.get(ScanStageCoverageRow, (scan.id, "visual", "worker-a"))\
                .status == "partial"
            assert repository.scan_checkpoint_stages(db, scan.id, require_provenance=True) == set()
    finally:
        engine.dispose()


def test_requested_audio_enrichment_without_inputs_is_not_unsupported(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fixture.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            scan = _scan(db, tmp_path / "synthetic.mkv")
            worker._record_stage(
                db, scan.id, "worker-a", "audio_qwen", "requested",
                {"qwen_model_requested": "qwen3-vl-4b-instruct-4bit"},
            )
            row = db.get(ScanStageCoverageRow, (scan.id, "audio_qwen", "worker-a"))
            assert row.status == "unknown"
            worker._record_stage(
                db, scan.id, "worker-a", "audio_qwen", "no_inputs",
                {"qwen_model_requested": "qwen3-vl-4b-instruct-4bit"},
            )
            assert row.status == "disabled"
            assert row.model_id is None
            assert json.loads(row.effective_settings_json)["skip_reason"] == "no_inputs"
    finally:
        engine.dispose()


def test_zero_finding_stage_completes_and_unavailable_model_does_not_run(
    tmp_path, monkeypatch
):
    media = tmp_path / "synthetic.mkv"
    media.write_bytes(b"synthetic")
    monkeypatch.setattr(runner.settings, "frames_dir", tmp_path / "frames")
    monkeypatch.setattr(runner, "compute_video_md5", lambda _path: "hash")
    monkeypatch.setattr(runner, "require_available", lambda _model: None)
    monkeypatch.setattr(runner, "qwen_enrichment_capability", lambda: SimpleNamespace(
        availability="available", enrichment_behavior="qwen_summary",
    ))
    monkeypatch.setattr(runner, "_scan_visual_isolated", lambda *_args, **_kwargs: [])
    events = []
    checkpoints = []
    runner.run_scan(
        db=object(), video_path=str(media), video_id="scan", video_duration_seconds=90,
        scan_audio=False, scan_video=True, video_model="image_safety_gate",
        status_updater=lambda *_args: None,
        findings_writer=lambda *_args: pytest.fail("no findings"),
        stage_callback=lambda stage, state, effective: events.append((stage, state, effective)),
        checkpoint_callback=checkpoints.append,
    )
    assert checkpoints == ["visual"]
    assert [(stage, state) for stage, state, _effective in events] == [
        ("visual_qwen", "requested"), ("visual", "started"), ("visual", "complete"),
    ]
    assert events[-1][2]["video_model"] == "image_safety_gate"
    assert events[-1][2]["duration_seconds"] == 90

    events.clear()
    monkeypatch.setattr(runner, "require_available", lambda _model: (
        (_ for _ in ()).throw(CapabilityUnavailableError("unavailable"))
    ))
    with pytest.raises(CapabilityUnavailableError):
        runner.run_scan(
            db=object(), video_path=str(media), video_id="scan", video_duration_seconds=90,
            scan_audio=False, scan_video=True, video_model="nudenet",
            status_updater=lambda *_args: None, findings_writer=lambda *_args: None,
            stage_callback=lambda stage, state, effective: events.append((stage, state, effective)),
        )
    assert [(stage, state) for stage, state, _effective in events] == [
        ("visual", "unsupported")
    ]


def test_decision_video_models_resolve_their_pinned_artifacts():
    qwen = stage_model_identity("visual", {"video_model": "qwen3-vl-4b"})
    d1 = stage_model_identity("visual", {"video_model": "d1-3b"})
    summary = stage_model_identity(
        "visual_qwen", {"qwen_model_requested": "qwen3-vl-8b-instruct-4bit"}
    )

    assert qwen == {
        "model_id": "qwen3-vl-4b",
        "model_revision": "2fd8dacbdb8f1e54b8c005f081ec5bf79c56376b",
        "model_digest": None,
        "quantization": "4bit",
    }
    assert (d1["model_revision"], d1["quantization"]) == (
        "05b80faf55536582ad5ffb2da7456f6a653acf71",
        None,
    )
    assert summary["model_revision"] == "defcdea7cc7a4b0858fea563cbbce171d328e457"
