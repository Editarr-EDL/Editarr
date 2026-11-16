from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository
from scanner.db import (
    Base,
    MediaItemRow,
    ReviewMcfDocumentRow,
    ReviewScanMembershipRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanMcfDocumentRow,
    ScanRow,
)
from scanner.models import (
    McfChannel,
    ReviewMcfDocument,
)
from scanner.persistence.repository.import_transaction import (
    refresh_imported_rows,
    rollback_on_import_error,
    stage_imported_review,
    stage_imported_scan,
)
from tests.mcf_import_support import _parsed_document, _source_timing

FIXTURES = Path(__file__).parent / "fixtures" / "mcf"





def test_mcf_import_commit_failure_rolls_back_every_owned_row(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        media = MediaItemRow(
            id="media",
            server_id="server",
            jellyfin_id="item",
            item_type="Movie",
            name="Movie",
        )
        db.add(media)
        db.commit()

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        parsed = _parsed_document([McfChannel.AUDIO])
        with pytest.raises(RuntimeError, match="injected commit failure"):
            repository.import_mcf_as_saved_review(
                db,
                media_item=media,
                resolved_local_path="/tmp/media.mkv",
                media_source_id=None,
                source_name="rollback.mcf",
                review_name="Rollback",
                parsed=parsed,
                catalog_mcf_document=ReviewMcfDocument(material_end_seconds=10),
                duration_seconds=10,
                **_source_timing(parsed),
            )

        assert db.query(ScanRow).count() == 0
        assert db.query(ScanFindingRow).count() == 0
        assert db.query(ScanMcfDocumentRow).count() == 0
        assert db.query(SavedReviewRow).count() == 0
        assert db.query(ReviewMcfDocumentRow).count() == 0


@pytest.mark.parametrize("failure", [None, "staging", "commit"])
def test_shared_import_staging_keeps_evidence_in_the_callers_transaction(
    tmp_path, monkeypatch, failure
):
    engine = create_engine(f"sqlite:///{tmp_path / 'staged.db'}")
    Base.metadata.create_all(engine)
    imported_at = datetime(2026, 10, 3, tzinfo=timezone.utc)
    try:
        with Session(engine) as db:
            media = MediaItemRow(
                id="media", server_id="server", jellyfin_id="item",
                item_type="Movie", name="Movie",
            )
            db.add(media)
            db.commit()
            commits = []
            original_commit = db.commit

            def commit():
                commits.append(True)
                if failure == "commit":
                    raise RuntimeError("injected commit failure")
                original_commit()

            monkeypatch.setattr(db, "commit", commit)
            expectation = (
                pytest.raises(RuntimeError, match=f"injected {failure} failure")
                if failure else nullcontext()
            )
            with expectation, rollback_on_import_error(db):
                scan = stage_imported_scan(
                    db, media_item=media, scan_number=7,
                    resolved_local_path="/tmp/media.mkv", media_source_id="version-1",
                    source_name="staged.mcf", review_name="Staged", duration_seconds=10,
                    has_audio=True, has_video=False, progress_message="Imported MCF",
                    imported_at=imported_at,
                    mcf_document=ScanMcfDocumentRow(
                        source_version="1.1.0", material_start_seconds=0,
                        material_end_seconds=10,
                    ),
                )
                review = stage_imported_review(
                    db, media_item=media, media_source_id="version-1",
                    review_name="Staged", imported_at=imported_at,
                )
                db.add_all([
                    ScanFindingRow(
                        scan_id=scan.id, category="language", subcategory="swearing",
                        start_seconds=1, end_seconds=2, confidence=None,
                        label="Evidence", source="audio", selected=True, edit_action="mute",
                    ),
                    ReviewScanMembershipRow(
                        review_id=review.id, scan_id=scan.id, role="audio", position=0,
                    ),
                ])
                db.flush()
                assert commits == []
                assert scan.scan_number == 7
                assert scan.imported_at == imported_at
                assert scan.audio_model == "imported" and scan.video_model is None
                assert review.media_source_id == "version-1"
                with Session(engine) as observer:
                    assert observer.query(ScanRow).count() == 0
                    assert observer.query(SavedReviewRow).count() == 0
                if failure == "staging":
                    raise RuntimeError("injected staging failure")
                db.commit()
                refresh_imported_rows(db, scan, review)
            assert len(commits) == (0 if failure == "staging" else 1)

        with Session(engine) as observer:
            expected = 0 if failure else 1
            for row in (
                ScanRow, ScanMcfDocumentRow, ScanFindingRow,
                SavedReviewRow, ReviewScanMembershipRow,
            ):
                assert observer.query(row).count() == expected
            assert observer.query(MediaItemRow).count() == 1
    finally:
        engine.dispose()


def test_mcf_import_evaluator_failure_rolls_back_every_owned_row(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        media = MediaItemRow(
            id="media",
            server_id="server",
            jellyfin_id="item",
            item_type="Movie",
            name="Movie",
        )
        db.add(media)
        db.commit()

        def fail_defaults(*_args, **_kwargs):
            raise ValueError("injected evaluator failure")

        monkeypatch.setattr(repository, "fill_filter_defaults", fail_defaults)
        parsed = _parsed_document([McfChannel.AUDIO])
        with pytest.raises(ValueError, match="injected evaluator failure"):
            repository.import_mcf_as_saved_review(
                db,
                media_item=media,
                resolved_local_path="/tmp/media.mkv",
                media_source_id=None,
                source_name="rollback.mcf",
                review_name="Rollback",
                parsed=parsed,
                catalog_mcf_document=ReviewMcfDocument(material_end_seconds=10),
                duration_seconds=10,
                **_source_timing(parsed),
            )

        assert db.query(ScanRow).count() == 0
        assert db.query(ScanFindingRow).count() == 0
        assert db.query(ScanMcfDocumentRow).count() == 0
        assert db.query(SavedReviewRow).count() == 0
        assert db.query(ReviewMcfDocumentRow).count() == 0


