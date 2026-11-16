"""Shared in-memory review fixture for cue retiming tests."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository
from scanner.db import Base, MediaItemRow, ScanFindingRow, ScanRow
from scanner.models import ReviewCreate, ReviewScanMembership
from scanner.pipeline.cache import compute_video_md5


@pytest.fixture
def retime_review(tmp_path, monkeypatch):
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    media = MediaItemRow(
        id="media",
        server_id="http://jellyfin.test",
        jellyfin_id="movie",
        item_type="Movie",
        name="Movie",
        path=str(media_path),
    )
    scan = ScanRow(
        id="scan",
        media_item_id=media.id,
        scan_number=1,
        origin="imported",
        source_path=str(media_path),
        resolved_local_path=str(media_path),
        video_hash=compute_video_md5(str(media_path)),
        duration_seconds=100,
        status="complete",
        scan_audio=True,
        scan_video=True,
    )
    db.add_all(
        [
            media,
            scan,
            ScanFindingRow(
                id=1,
                scan_id=scan.id,
                category="violence",
                subcategory="violence",
                start_seconds=20,
                end_seconds=40,
                confidence=0.8,
                label="Linked",
                source="visual",
                selected=False,
                edit_action="skip",
            ),
        ]
    )
    db.commit()
    review = repository.create_saved_review(
        db,
        media_item_id=media.id,
        media_source_id=None,
        duration_seconds=100,
        data=ReviewCreate(
            name="Retime",
            memberships=[ReviewScanMembership(scan_id=scan.id, role="both")],
        ),
    )
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 100.0)
    yield db, review, media_path
    db.close()
    engine.dispose()
