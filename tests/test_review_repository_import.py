import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository
from scanner.db import (
    Base,
    CreditsDetectionRunRow,
    MediaItemRow,
    ReviewAcceptedCreditsRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.formats.edl import parse_edl_content
from scanner.models import (
    ExportFormat,
    ReviewCreate,
    ReviewDuplicate,
    ReviewFilterInput,
    ReviewSave,
    ReviewScanMembership,
    ReviewSynchronization,
)
from tests.review_repository_support import review_db


def test_import_facades_preserve_the_focused_workflow_exports():
    from scanner.persistence.repository import (
        import_edl,
        import_mcf,
        import_metadata,
        repository_imports,
    )

    assert repository.import_edl_as_saved_review is import_edl.import_edl_as_saved_review
    assert repository.import_mcf_as_saved_review is import_mcf.import_mcf_as_saved_review
    assert repository._import_mcf_as_saved_review is import_mcf._import_mcf_as_saved_review
    assert repository._mcf_metadata_notices is import_metadata._mcf_metadata_notices
    assert repository_imports.import_edl_as_saved_review is repository.import_edl_as_saved_review
    assert repository_imports.import_mcf_as_saved_review is repository.import_mcf_as_saved_review


def test_import_commit_failure_rolls_back_the_complete_aggregate(tmp_path, monkeypatch):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        media = db.get(MediaItemRow, "media")
        media.path = str(media_path)
        db.commit()

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            repository.import_edl_as_saved_review(
                db,
                media_item=media,
                resolved_local_path=str(media_path),
                media_source_id=None,
                source_name="input.edl",
                review_name="Imported",
                duration_seconds=2,
                parsed=parse_edl_content(b"1 2 1\n"),
            )

        assert db.query(SavedReviewRow).count() == 0
        assert db.query(ScanRow).filter(ScanRow.origin == "imported").count() == 0
        assert db.query(ScanFindingRow).count() == 1
    finally:
        db.close()


def test_imported_review_and_export_survive_file_backed_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'restart.db'}"
    media_path = tmp_path / "restart.mkv"
    media_path.write_bytes(b"media")
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            media = MediaItemRow(
                id="restart-media",
                server_id="http://jellyfin.test",
                jellyfin_id="restart-item",
                item_type="Movie",
                name="Restart Movie",
                path=str(media_path),
            )
            db.add(media)
            db.commit()
            imported = repository.import_edl_as_saved_review(
                db,
                media_item=media,
                resolved_local_path=str(media_path),
                media_source_id=None,
                source_name="restart-input.edl",
                review_name="Imported before restart",
                duration_seconds=4,
                parsed=parse_edl_content(
                    b"## profanity: Imported mute\n1 2 1\n"
                    b"## violence: Imported skip\n3 4 3\n"
                ),
            )
            repository.rename_scan(db, imported.scan.id, "Reusable imported evidence")
            saved = repository.save_saved_review(
                db,
                imported.review.id,
                ReviewSave(
                    revision=imported.review.revision,
                    name="Edited before restart",
                    memberships=imported.review.memberships,
                    filters=[
                        ReviewFilterInput(
                            **(
                                review_filter.model_dump()
                                | {
                                    "label": f"Persisted {index}",
                                    "selected": index == 0,
                                }
                            )
                        )
                        for index, review_filter in enumerate(imported.review.filters)
                    ],
                    mcf_document=imported.review.mcf_document,
                    synchronization=imported.review.synchronization,
                ),
            )
            repository.export_saved_review_edl(db, saved.id, saved.revision)
            review_id = saved.id
            scan_id = imported.scan.id
    finally:
        engine.dispose()

    restarted_engine = create_engine(database_url)
    try:
        with Session(restarted_engine) as restarted_db:
            review = repository.get_saved_review(restarted_db, review_id)
            assert review is not None
            assert review.name == "Edited before restart"
            assert review.revision == 1
            assert review.content_revision == 1
            assert [
                (membership.scan_id, membership.role)
                for membership in review.memberships
            ] == [(scan_id, "both")]
            assert [
                (review_filter.label, review_filter.selected)
                for review_filter in review.filters
            ] == [
                ("Persisted 0", True),
                ("Persisted 1", False),
            ]
            assert review.exports.exports[ExportFormat.EDL].current is True
            assert review.exports.exports[ExportFormat.EDL].exported_format_revision == 1

            scan = restarted_db.get(ScanRow, scan_id)
            assert scan is not None
            assert scan.name == "Reusable imported evidence"
            assert scan.origin == "imported"
            assert scan.import_source_name == "restart-input.edl"
            assert scan.imported_at is not None

            status = repository.media_item_export_status(
                restarted_db,
                server_id="http://jellyfin.test",
                jellyfin_item_id="restart-item",
            ).exports[ExportFormat.EDL]
            assert status.review_id == review_id
            assert status.exported_format_revision == 1
            assert [
                (reference.id, reference.name, reference.origin)
                for reference in status.exported_scans
            ] == [
                (scan_id, "Reusable imported evidence", "imported")
            ]
            assert "Persisted 0" in media_path.with_suffix(".edl").read_text()
            assert "Persisted 1" not in media_path.with_suffix(".edl").read_text()
    finally:
        restarted_engine.dispose()


def test_review_synchronization_round_trip_revision_and_runtime_guard():
    db = review_db()
    try:
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(name="Timing"),
        )
        assert created.synchronization == ReviewSynchronization(
            start_seconds=0, end_seconds=100, end_mode="file_end"
        )
        assert created.mcf_document.material_end_seconds is None

        saved = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=0,
                name=created.name,
                memberships=created.memberships,
                filters=[],
                mcf_document=created.mcf_document,
                synchronization=created.synchronization.model_copy(
                    update={"end_seconds": 90}
                ),
            ),
        )
        assert saved is not None
        assert (
            saved.revision,
            saved.content_revision,
            saved.edl_content_revision,
            saved.mcf_content_revision,
        ) == (1, 1, 0, 0)

        with pytest.raises(ValueError, match="exceeds media duration"):
            repository.save_saved_review(
                db,
                saved.id,
                ReviewSave(
                    revision=1,
                    name=saved.name,
                    memberships=saved.memberships,
                    filters=[],
                    mcf_document=saved.mcf_document,
                    synchronization=saved.synchronization.model_copy(
                        update={"end_seconds": 101}
                    ),
                ),
            )
        assert repository.get_saved_review(db, created.id).revision == 1
    finally:
        db.close()


def test_runtime_is_required_without_a_matching_probed_scan():
    db = review_db()
    try:
        db.get(ScanRow, "scan").duration_seconds = None
        db.commit()
        with pytest.raises(repository.MediaRuntimeUnavailable):
            repository.create_saved_review(
                db,
                media_item_id="media",
                media_source_id=None,
                data=ReviewCreate(name="No runtime"),
            )
        assert db.query(SavedReviewRow).count() == 0
    finally:
        db.close()


def test_accepted_credits_round_trip_duplicate_remove_and_scan_serialization():
    db = review_db()
    try:
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Credits",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        scan = db.get(ScanRow, "scan")
        scan.scan_mode = "credits_detection"
        scan.credits_detection = CreditsDetectionRunRow(
            scan_id=scan.id,
            configuration_json=json.dumps(
                {
                    "detector_version": "credits-v1",
                    "tail_window_seconds": 300,
                    "frame_sample_interval_seconds": 1,
                    "qwen_model": None,
                }
            ),
            detector_version="credits-v1",
            cache_key="cache-v1",
            result_json=json.dumps(
                {"outcome": "no_candidate", "candidates": [], "best_candidate_id": None}
            ),
            completed_at=datetime.now(timezone.utc),
        )
        accepted_at = datetime.now(timezone.utc)
        db.add(
            ReviewAcceptedCreditsRow(
                review_id=created.id,
                scan_id=scan.id,
                source_finding_id=1,
                candidate_id="candidate-1",
                detected_boundary_seconds=90,
                accepted_boundary_seconds=91,
                confidence=0.8,
                method="hybrid",
                accepted_at=accepted_at,
            )
        )
        db.commit()

        loaded = repository.get_saved_review(db, created.id)
        assert loaded is not None and loaded.accepted_credits is not None
        assert loaded.accepted_credits.detected_boundary_seconds == 90
        assert loaded.accepted_credits.accepted_boundary_seconds == 91
        credits_scan = next(
            value for value in repository.list_scan_history(db) if value.id == scan.id
        )
        assert credits_scan.scan_mode == "credits_detection"
        assert credits_scan.credits_detection is not None
        assert credits_scan.credits_detection.result is not None
        assert credits_scan.credits_detection.result.outcome == "no_candidate"

        duplicate = repository.duplicate_saved_review(
            db, loaded.id, ReviewDuplicate(revision=0, name="Credits copy")
        )
        assert duplicate is not None
        assert duplicate.accepted_credits == loaded.accepted_credits

        saved = repository.save_saved_review(
            db,
            loaded.id,
            ReviewSave(
                revision=0,
                name=loaded.name,
                memberships=loaded.memberships,
                filters=[ReviewFilterInput(**value.model_dump()) for value in loaded.filters],
                mcf_document=loaded.mcf_document,
                synchronization=loaded.synchronization,
                accepted_credits=None,
            ),
        )
        assert saved is not None and saved.accepted_credits is None
        assert (
            saved.revision,
            saved.content_revision,
            saved.edl_content_revision,
            saved.mcf_content_revision,
        ) == (1, 1, 0, 0)
        assert db.get(CreditsDetectionRunRow, scan.id) is not None
    finally:
        db.close()
