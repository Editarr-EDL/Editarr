"""Workers refuse replacement bytes before analysis and before committing results."""

from hashlib import sha256
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scanner import repository, worker
from scanner.db import Base, MediaItemRow, ScanRow
from scanner.persistence.repository.media_identity_store import get_version, observe_version
from scanner.runtime import media_fingerprints
from scanner.runtime.media_fingerprints import content_digest, sampled_media_fingerprint
from tests.test_dedicated_worker import _scan_with_queue
from tests.test_dedicated_worker import isolated_worker_paths as isolated_worker_paths


@pytest.mark.parametrize("replacement", ["before", "during", "unwitnessed"])
def test_worker_refuses_a_changed_pinned_media_file(monkeypatch, tmp_path, replacement):
    path = tmp_path / "video.mkv"
    path.write_bytes(b"original media")
    engine = create_engine(f"sqlite:///{tmp_path / 'scanner.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as db:
            scan, _ = _scan_with_queue(db)
            row = db.get(ScanRow, scan.id)
            row.resolved_local_path = str(path)
            media = db.get(MediaItemRow, row.media_item_id)
            expected = (
                sha256(path.read_bytes()).hexdigest()
                if replacement == "unwitnessed"
                else content_digest(str(path))
            )
            observe_version(
                db,
                media=media,
                media_source_id=row.media_source_id,
                source_path=row.source_path,
                resolved_path=str(path),
                content_sha256=expected,
                scan_id=scan.id,
            )
            db.commit()
            assert repository.reserve_scan_worker(db, scan.id, token="worker-token")

        def fake_scan(db, _path, **callbacks):
            if replacement != "during":
                pytest.fail("The worker analyzed unverified media")
            assert callbacks["media_cache_key"] == expected
            path.write_bytes(b"replacement video")
            callbacks["status_updater"](db, scan.id, "complete")
            pytest.fail("The worker committed a result after its input changed")

        if replacement == "before":
            path.write_bytes(b"replacement video")

        def unexpected_full_read(*args, **kwargs):
            pytest.fail("A worker tried to recover a SHA-only version with a full-file read")

        monkeypatch.setattr(media_fingerprints, "content_digest", unexpected_full_read)
        monkeypatch.setattr(
            worker,
            "WorkerControlServer",
            lambda *_args: SimpleNamespace(start=lambda: None, close=lambda: None),
        )
        monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
        monkeypatch.setattr(worker, "run_scan", fake_scan)
        assert worker.run_scan_worker(scan.id, "worker-token") == 0
        with factory() as db:
            row = db.get(ScanRow, scan.id)
            assert row.status == "failed"
            assert "media file changed" in row.error_message
            assert repository.get_scan_queue_item_by_scan_id(db, scan.id) is None
    finally:
        engine.dispose()


def test_worker_fails_expired_audio_selection_and_requires_a_new_scan(monkeypatch, tmp_path):
    from scanner.pipeline import audio_tracks
    from scanner.runtime.scan_version import STALE_AUDIO_SELECTION

    path = tmp_path / "audio.mkv"
    path.write_bytes(b"original media")
    engine = create_engine(f"sqlite:///{tmp_path / 'scanner.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as db:
            scan, queued = _scan_with_queue(db)
            row = db.get(ScanRow, scan.id)
            row.resolved_local_path = str(path)
            db.commit()
            assert repository.reserve_scan_worker(db, scan.id, token="worker-token")

        track = queued.requested_audio_selection.selection().track
        inventory = audio_tracks.AudioTrackInventory("b" * 64, (track,))
        monkeypatch.setattr(audio_tracks, "probe_audio_tracks", lambda _path: inventory)
        monkeypatch.setattr(
            worker,
            "WorkerControlServer",
            lambda *_args: SimpleNamespace(start=lambda: None, close=lambda: None),
        )
        monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
        assert worker.run_scan_worker(scan.id, "worker-token") == 0
        with factory() as db:
            row = db.get(ScanRow, scan.id)
            assert row.status == "failed"
            assert row.error_message == STALE_AUDIO_SELECTION
            assert row.analyzed_audio_selection_json is None
            assert repository.get_scan_queue_item_by_scan_id(db, scan.id) is None
    finally:
        engine.dispose()


@pytest.mark.parametrize(("changed", "cached"), [(False, False), (False, True), (True, False)])
def test_worker_pins_deferred_file_after_job_is_queued(monkeypatch, tmp_path, changed, cached):
    from scanner.formats.community.fingerprints import SAMPLE_BYTES
    from scanner.persistence.repository.media_identity_store import scan_version_id
    from scanner.runtime import scan_version
    from scanner.runtime.scan_version import (
        file_generation,
        stage_scan_generation,
    )

    path = tmp_path / "queued.mkv"
    with path.open("wb") as media:
        media.truncate(24 * SAMPLE_BYTES)
    if cached:
        sampled_media_fingerprint(path)
    # The sample reads in MiB increments. Report at 0 and 16 MiB due to throttling.
    ticks = iter([0.0, *([0.4] * 15), 1.1, *([1.5] * 8)])
    monkeypatch.setattr(scan_version, "monotonic", lambda: next(ticks))
    engine = create_engine(f"sqlite:///{tmp_path / 'scanner.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as db:
            scan, _ = _scan_with_queue(db)
            row = db.get(ScanRow, scan.id)
            row.resolved_local_path = str(path)
            stage_scan_generation(db, scan.id, str(path), file_generation(str(path)))
            db.commit()
            assert scan_version_id(db, scan.id) is None
            assert repository.get_scan_queue_item_by_scan_id(db, scan.id)
            assert repository.reserve_scan_worker(db, scan.id, token="worker-token")
        if changed:
            path.write_bytes(b"replacement media")

        observed_progress = []
        update_progress = worker.update_scan_progress

        def record_progress(db, scan_id, update, **kwargs):
            update_progress(db, scan_id, update, **kwargs)
            with factory() as reader:
                visible = repository.get_scan_queue_item_by_scan_id(reader, scan_id)
                observed_progress.append(visible.progress_percent)

        def fake_scan(db, _path, **callbacks):
            assert not changed, "The worker analyzed a replacement"
            assert scan_version_id(db, scan.id)
            row = db.get(ScanRow, scan.id)
            assert row.progress_stage == ("setup" if cached else "hashing")
            saved_percent = row.progress_percent
            update_progress(db, scan.id, {"stage": "hashing", "percent": 95}, worker_token="old")
            assert db.get(ScanRow, scan.id).progress_percent == saved_percent
            assert callbacks["media_cache_key"] == sampled_media_fingerprint(path).digest
            callbacks["status_updater"](db, scan.id, "complete")
            update_progress(
                db, scan.id, {"stage": "hashing", "percent": 99}, worker_token="worker-token"
            )
            assert db.get(ScanRow, scan.id).progress_percent == saved_percent

        monkeypatch.setattr(
            worker,
            "WorkerControlServer",
            lambda *_args: SimpleNamespace(start=lambda: None, close=lambda: None),
        )
        monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
        monkeypatch.setattr(worker, "run_scan", fake_scan)
        monkeypatch.setattr(worker, "update_scan_progress", record_progress)
        assert worker.run_scan_worker(scan.id, "worker-token") == 0
        assert observed_progress == ([] if cached or changed else [0, 66.67])
        with factory() as db:
            row = db.get(ScanRow, scan.id)
            assert row.status == ("failed" if changed else "complete")
            version_id = scan_version_id(db, scan.id)
            assert bool(version_id) is not changed
            if not changed:
                version = get_version(db, version_id)
                assert version.content_sha256 is None
                sample = sampled_media_fingerprint(path)
                assert version.sampled_md5 == row.video_hash == sample.digest
    finally:
        engine.dispose()


def test_reuse_checks_the_legacy_audio_scan_against_the_current_inventory(monkeypatch, tmp_path):
    from scanner.modeling.model_audio_tracks import AudioSelection
    from scanner.persistence.repository.repository_audio_tracks import (
        record_analyzed_audio_selection,
    )
    from scanner.pipeline import audio_tracks
    from scanner.runtime.scan_version import completed_scan_digest, file_generation

    path = tmp_path / "movie.mkv"
    path.write_bytes(b"original media")
    engine = create_engine(f"sqlite:///{tmp_path / 'scanner.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as db:
            scan, _ = _scan_with_queue(db)
            row = db.get(ScanRow, scan.id)
            row.resolved_local_path = str(path)
            row.scan_audio = True
            selection = AudioSelection.model_validate(
                {
                    "file_version": "a" * 64,
                    "track": {"stream_index": 1, "disposition": 1},
                }
            )
            row.requested_audio_selection_json = selection.model_dump_json()
            digest = content_digest(path)
            observe_version(
                db,
                media=row.media_item,
                media_source_id=row.media_source_id,
                source_path=row.source_path,
                resolved_path=str(path),
                content_sha256=digest,
                scan_id=scan.id,
            )
            record_analyzed_audio_selection(db, scan.id, selection, playback_offset_seconds=0)
            row.status = "complete"
            db.commit()
            target = {
                "server_id": row.media_item.server_id,
                "item_id": row.media_item.jellyfin_id,
                "media_source_id": row.media_source_id,
                "path": path,
            }
            inventory = SimpleNamespace(file_version=selection.file_version)
            monkeypatch.setattr(audio_tracks, "probe_audio_tracks", lambda _path: inventory)
            assert completed_scan_digest(db, **target) == digest
            inventory.file_version = "b" * 64
            assert completed_scan_digest(db, **target) is None
            selected_evidence = (file_generation(str(path)), selection.file_version)
            assert completed_scan_digest(db, **target, audio_evidence=selected_evidence) == digest
            path.write_bytes(b"different clip")
            assert completed_scan_digest(db, **target, audio_evidence=selected_evidence) is None
    finally:
        engine.dispose()
