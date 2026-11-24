"""Keep Apple Speech captions and alignment tied to recorded scan inputs."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.db import ScanRow
from scanner.http.reuse_alignment import _transcript_cache_by_generation
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.persistence.repository.repository_audio_tracks import record_analyzed_audio_selection
from scanner.persistence.repository.repository_checkpoints import mark_scan_checkpoint
from scanner.persistence.repository.repository_provenance import record_scan_stage
from scanner.pipeline import audio, audio_tracks, runner
from scanner.pipeline.audio_tracks import AudioTrack, AudioTrackInventory
from scanner.pipeline.cache import transcription_cache_path
from scanner.runtime.subtitle_transcript import cached_segments, reviewed_audio_sources
from scanner.runtime.version_reuse_audio import target_transcript_ready
from tests.review_repository_support import review_db


@pytest.fixture
def apple_scans(tmp_path, monkeypatch):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"synthetic media")
    track = AudioTrack(1, "eng", None, "aac", 2, 0, 0.0, 20.0, "1/48000")
    inventory = AudioTrackInventory("a" * 64, (track,))
    selection = inventory.select(1)
    requested = AudioSelection.from_selection(selection)
    monkeypatch.setattr(runner.settings, "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(runner.settings, "frames_dir", tmp_path / "frames")
    monkeypatch.setattr(runner.settings, "data_dir", tmp_path)
    monkeypatch.setattr(runner, "require_available", lambda _model: None)
    monkeypatch.setattr(audio_tracks, "probe_audio_tracks", lambda _path: inventory)
    monkeypatch.setattr(audio, "audio_playback_offset", lambda *_a, **_kw: 0)
    monkeypatch.setattr(
        runner, "extract_audio", lambda _input, output, **_kw: Path(output).write_bytes(b"audio")
    )

    with review_db() as db:
        scans = {}
        paths = {}
        for index, (scan_id, locale) in enumerate((("scan", "en-us"), ("rescan", "es-es")), 1):
            scan = db.get(ScanRow, scan_id)
            if scan is None:
                scan = ScanRow(
                    id=scan_id, media_item_id="media", scan_number=index, source_path=str(media)
                )
                db.add(scan)
            scan.status = "queued"
            scan.scan_audio = True
            scan.scan_video = False
            scan.audio_model = "apple_speech"
            scan.requested_audio_selection_json = requested.model_dump_json()
            scan.resolved_local_path = str(media)
            db.commit()

            key = f"apple_speech_v1.{locale}"
            path = transcription_cache_path(selection.extraction_cache_key(), key)
            execution_id = f"attempt-{index}"
            monkeypatch.setattr(runner.settings, "apple_speech_locale", locale)

            def analyze(_path, _scan, **kwargs):
                assert kwargs["apple_speech_locale"] == locale
                assert kwargs["transcript_cache_path_value"] == str(path)
                word = {"word": locale, "start_seconds": 1, "end_seconds": 2}
                segment = {"text": locale, "start_seconds": 1, "end_seconds": 2, "words": [word]}
                path.write_text(json.dumps([segment]), encoding="utf-8")
                return []

            def stage(stage, state, settings):
                record_scan_stage(db, scan_id, stage, execution_id, state, settings)
                if stage == "audio_scan" and state == "started":
                    assert settings["transcript_cache_key"] == key
                    # The cache key and worker must use the locale already selected by the runner.
                    monkeypatch.setattr(runner.settings, "apple_speech_locale", "fr-fr")

            def status(_db, _scan_id, state):
                scan.status = state
                db.commit()

            monkeypatch.setattr(runner, "_scan_audio_isolated", analyze)
            runner.run_scan(
                db=db,
                video_path=str(media),
                video_id=scan_id,
                video_duration_seconds=20,
                scan_video=False,
                audio_model="apple_speech",
                audio_selection=selection,
                status_updater=status,
                findings_writer=lambda *_args: None,
                analyzed_audio_callback=lambda chosen: record_analyzed_audio_selection(
                    db, scan_id, AudioSelection.from_selection(chosen)
                ),
                checkpoint_callback=lambda name: mark_scan_checkpoint(
                    db, scan_id, name, execution_id=execution_id
                ),
                stage_callback=stage,
            )
            scans[scan_id] = scan
            paths[scan_id] = path
        monkeypatch.setattr(runner.settings, "apple_speech_locale", "es-es")
        yield db, scans, paths, media, selection


def test_saved_review_uses_completed_attempt_and_never_substitutes_locale(apple_scans):
    db, scans, paths, media, _selection = apple_scans
    # A different attempt cannot override the one selected by the checkpoint.
    record_scan_stage(
        db, "scan", "audio_scan", "other-attempt", "started",
        {"transcript_cache_key": "apple_speech_v1.es-es"},
    )
    record_scan_stage(db, "scan", "audio_scan", "other-attempt", "complete", {})

    def review_audio(scan_id):
        return reviewed_audio_sources(
            db, [SimpleNamespace(role="audio", scan_id=scan_id)], scans, media
        )[0]

    assert cached_segments(review_audio("scan"))[0]["text"] == "en-us"
    assert cached_segments(review_audio("rescan"))[0]["text"] == "es-es"
    saved_checkpoint = scans["scan"].checkpoint_state
    scans["scan"].checkpoint_state = "{}"
    assert cached_segments(review_audio("scan")) is None
    scans["scan"].checkpoint_state = saved_checkpoint

    paths["scan"].unlink()
    assert cached_segments(review_audio("scan")) is None


def test_target_transcript_requires_recorded_apple_scan_for_exact_generation(apple_scans):
    _db, scans, paths, _media, selection = apple_scans
    source = scans["scan"]
    target_scans = [scans["rescan"]]
    assert _transcript_cache_by_generation(source, selection, [], None)[2] is False
    assert _transcript_cache_by_generation(source, selection, target_scans, None)[2] is True
    moved = replace(selection, file_version="b" * 64)
    # A different generation can have an identically named cache. The scan never used it.
    unproven = transcription_cache_path(moved.extraction_cache_key(), "apple_speech_v1.es-es")
    unproven.write_bytes(paths["rescan"].read_bytes())
    assert _transcript_cache_by_generation(source, moved, target_scans, None)[2] is False

    recorded_selection, candidates, ready = _transcript_cache_by_generation(
        source, moved, target_scans, "verified-version"
    )
    assert ready and recorded_selection == selection
    assert target_transcript_ready(recorded_selection, candidates) is True
    assert target_transcript_ready(moved, candidates) is False

    paths["rescan"].unlink()
    _, _, ready = _transcript_cache_by_generation(source, moved, target_scans, "verified-version")
    assert not ready
