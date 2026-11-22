"""Workers refuse replacement bytes before analysis and before committing results."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scanner import repository, worker
from scanner.db import Base, MediaItemRow, ScanRow
from scanner.persistence.repository.media_identity_store import observe_version
from scanner.runtime.media_fingerprints import content_digest
from tests.test_dedicated_worker import _scan_with_queue
from tests.test_dedicated_worker import isolated_worker_paths as isolated_worker_paths


@pytest.mark.parametrize("replacement", ["before", "during"])
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
            expected = content_digest(str(path))
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
            if replacement == "before":
                pytest.fail("The worker analyzed a replacement file")
            assert callbacks["content_sha256"] == expected
            path.write_bytes(b"replacement video")
            callbacks["status_updater"](db, scan.id, "complete")
            pytest.fail("The worker committed a result after its input changed")

        if replacement == "before":
            path.write_bytes(b"replacement video")
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


@pytest.mark.parametrize("changed", [False, True])
def test_worker_pins_deferred_file_after_job_is_queued(monkeypatch, tmp_path, changed):
    from scanner.persistence.repository.media_identity_store import scan_version_id
    from scanner.runtime.scan_version import file_generation, stage_scan_generation

    path = tmp_path / "queued.mkv"
    path.write_bytes(b"original media")
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

        def fake_scan(db, _path, **callbacks):
            assert not changed, "The worker analyzed a replacement"
            assert scan_version_id(db, scan.id)
            assert db.get(ScanRow, scan.id).progress_stage == "hashing"
            assert callbacks["content_sha256"] == content_digest(path)
            callbacks["status_updater"](db, scan.id, "complete")

        monkeypatch.setattr(
            worker,
            "WorkerControlServer",
            lambda *_args: SimpleNamespace(start=lambda: None, close=lambda: None),
        )
        monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
        monkeypatch.setattr(worker, "run_scan", fake_scan)
        assert worker.run_scan_worker(scan.id, "worker-token") == 0
        with factory() as db:
            assert db.get(ScanRow, scan.id).status == ("failed" if changed else "complete")
            assert bool(scan_version_id(db, scan.id)) is not changed
    finally:
        engine.dispose()
