import subprocess
from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository
from scanner.db import Base, ScanRow
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.models import (
    EditAction,
    FindingCreate,
    McfCategoryFamily,
    ReviewCreate,
    ReviewScanMembership,
    Severity,
)
from scanner.persistence.repository import repository_scan_lifecycle
from scanner.persistence.repository.repository_audio_tracks import record_analyzed_audio_selection
from scanner.pipeline import audio, audio_tracks, runner
from scanner.pipeline.audio_tracks import AudioSelectionError, AudioTrack, AudioTrackInventory
from scanner.runtime.runtime_support import resolve_ffmpeg
from tests.editarr_support import db as db


def inventory():
    return AudioTrackInventory(
        "a" * 64,
        tuple(
            AudioTrack(index, "eng", title, "aac", 2, 0, 0.0, 2.0, "1/48000")
            for index, title in ((1, "Main"), (2, "Commentary"))
        ),
    )


def test_durable_actual_track_survives_reload_and_cannot_be_relabelled(tmp_path, monkeypatch):
    observed = inventory()
    monkeypatch.setattr(repository_scan_lifecycle, "probe_audio_tracks", lambda _path: observed)
    engine = create_engine(f"sqlite:///{tmp_path / 'execution.db'}")
    Base.metadata.create_all(engine)
    selected = AudioSelection.from_selection(observed.select(2))
    try:
        with Session(engine) as db:
            media = repository.upsert_media_item(
                db,
                server_id="fixture",
                jellyfin_id="generated",
                library_id=None,
                item_type="Video",
                name="Generated",
                path="/synthetic.mp4",
            )
            scan = repository.create_scan(
                db,
                media_item=media,
                resolved_local_path="/synthetic.mp4",
                scan_audio=True,
                audio_model="whisper",
                audio_filter="better_profanity",
                scan_video=False,
                video_model=None,
                audio_selection=selected,
            )
            queued = repository.enqueue_scan(db, scan.id)
            assert queued.requested_audio_selection == selected
            assert queued.analyzed_audio_selection is None
            record_analyzed_audio_selection(db, scan.id, selected)
        with Session(engine) as db:
            restored = repository.get_scan_queue_item_by_scan_id(db, scan.id)
            assert (
                restored.requested_audio_selection == restored.analyzed_audio_selection == selected
            )
            record_analyzed_audio_selection(db, scan.id, selected)
            with pytest.raises(AudioSelectionError):
                record_analyzed_audio_selection(
                    db, scan.id, AudioSelection.from_selection(observed.select(1))
                )
            assert (
                db.get(ScanRow, scan.id).analyzed_audio_selection_json == selected.model_dump_json()
            )
    finally:
        engine.dispose()


def test_delayed_selected_audio_findings_persist_and_export_on_playback_timeline(
    db, tmp_path, monkeypatch
):
    source = tmp_path / "delayed-secondary.mp4"
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=25:duration=5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-itsoffset",
            "2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=1",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-output_ts_offset",
            "5",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    inventory = audio_tracks.probe_audio_tracks(source)
    selection = inventory.select(2)
    assert len(inventory.tracks) == 2
    assert inventory.tracks[1].start_seconds == pytest.approx(7.0, abs=0.1)
    import av

    with av.open(str(source)) as container:
        assert container.start_time is not None
        container_start = container.start_time / av.time_base
        assert container_start > 4.0
    playback_offset = audio.audio_playback_offset(str(source), selection)
    assert playback_offset == pytest.approx(2.0, abs=0.1)

    media_item = repository.upsert_media_item(
        db,
        server_id="fixture",
        jellyfin_id="delayed-audio",
        library_id=None,
        item_type="Video",
        name="Delayed audio",
        path=str(source),
    )
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(source),
        media_source_id="generated-source",
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_model=None,
        audio_selection=AudioSelection.from_selection(selection),
    )
    cached_audio = tmp_path / "cache" / "selected.wav"
    monkeypatch.setattr(runner.settings, "frames_dir", tmp_path / "frames")
    monkeypatch.setattr(runner, "get_video_duration", lambda _path: 5.0)
    monkeypatch.setattr(runner, "compute_video_md5", lambda _path: "synthetic-container")
    monkeypatch.setattr(runner, "cached_audio_path", lambda _identity: cached_audio)
    monkeypatch.setattr(
        runner,
        "transcription_cache_path",
        lambda _identity, _model: tmp_path / "cache" / "transcript.json",
    )
    monkeypatch.setattr(
        runner,
        "_scan_audio_isolated",
        lambda *_args, **_kwargs: [
            FindingCreate(
                video_id=scan.id,
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=0.25,
                end_seconds=0.5,
                severity=Severity.MEDIUM,
                confidence=0.9,
                label="dang",
                source="audio",
                edit_action=EditAction.MUTE,
            )
        ],
    )

    runner.run_scan(
        db=db,
        video_path=str(source),
        scan_audio=True,
        scan_video=False,
        video_id=scan.id,
        audio_selection=selection,
        status_updater=lambda _db, scan_id, status: repository.update_scan_status(
            db, scan_id, status
        ),
        findings_writer=repository.create_scan_findings_batch,
        analyzed_audio_callback=lambda actual: record_analyzed_audio_selection(
            db, scan.id, AudioSelection.from_selection(actual)
        ),
    )

    saved = repository.get_scan_findings(db, scan.id).findings[0]
    assert saved.start_seconds == pytest.approx(playback_offset + 0.25, abs=0.001)
    assert saved.end_seconds == pytest.approx(playback_offset + 0.5, abs=0.001)
    review = repository.create_saved_review(
        db,
        media_item_id=media_item.id,
        media_source_id="generated-source",
        duration_seconds=5,
        data=ReviewCreate(
            name="Delayed audio",
            memberships=[ReviewScanMembership(scan_id=scan.id, role="audio")],
        ),
    )
    repository.export_saved_review_edl(db, review.id, review.revision)
    exported_range = next(
        line
        for line in source.with_suffix(".edl").read_text().splitlines()
        if not line.startswith("#")
    )
    start, end, action = exported_range.split()
    assert float(start) == pytest.approx(saved.start_seconds, abs=0.001)
    assert float(end) == pytest.approx(saved.end_seconds, abs=0.001)
    assert action == "1"


@pytest.mark.parametrize("fails", [False, True])
def test_runner_scopes_cache_to_track_and_records_actual_only_after_success(
    tmp_path, monkeypatch, fails
):
    observed = inventory()
    source = tmp_path / "generated.mp4"
    source.write_bytes(b"synthetic")
    monkeypatch.setattr(audio_tracks, "probe_audio_tracks", lambda _path: observed)
    monkeypatch.setattr(audio, "audio_playback_offset", lambda _path, _selection, **_kwargs: 0.0)
    monkeypatch.setattr(runner.settings, "frames_dir", tmp_path / "frames")
    monkeypatch.setattr(runner, "get_video_duration", lambda _path: 2.0)
    monkeypatch.setattr(runner, "compute_video_md5", lambda _path: "same-container")
    seen = []
    actual = []

    def cache_path(key):
        path = tmp_path / key / "audio.wav"
        path.parent.mkdir(exist_ok=True)
        return path

    def extract(_path, output, **kwargs):
        from pathlib import Path

        Path(output).write_bytes(str(kwargs["audio_selection"].track.stream_index).encode())

    def analyze(path, _scan, **kwargs):
        seen.append((path, kwargs["transcript_cache_path_value"]))
        if fails:
            raise RuntimeError("inference failed")
        return []

    monkeypatch.setattr(runner, "cached_audio_path", cache_path)
    monkeypatch.setattr(
        runner, "transcription_cache_path", lambda key, model: tmp_path / key / f"{model}.json"
    )
    monkeypatch.setattr(runner, "extract_audio", extract)
    monkeypatch.setattr(runner, "_scan_audio_isolated", analyze)
    for index in (1, 2):
        selection = observed.select(index)
        arguments = dict(
            db=object(),
            video_path=str(source),
            video_id="scan",
            scan_video=False,
            status_updater=lambda *_args: None,
            findings_writer=lambda *_args: None,
            audio_selection=selection,
            analyzed_audio_callback=actual.append,
        )
        if fails:
            with pytest.raises(RuntimeError, match="inference failed"):
                runner.run_scan(**arguments)
        else:
            runner.run_scan(**arguments)
    assert len(seen) == 2 and seen[0][0] != seen[1][0] and seen[0][1] != seen[1][1]
    assert [item.track.stream_index for item in actual] == ([] if fails else [1, 2])
    monkeypatch.setattr(
        audio_tracks, "probe_audio_tracks", lambda _path: replace(observed, file_version="b" * 64)
    )
    with pytest.raises(AudioSelectionError, match="file changed"):
        runner.run_scan(**arguments)
