from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository
from scanner.credits.credits_detector import (
    ChapterMarker,
    default_configuration,
    detect_from_observations,
)
from scanner.db import (
    Base,
    MediaItemExportRow,
    MediaItemRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.mcf import McfCueEntry, McfParseResult, parse_mcf_content
from scanner.models import (
    AcceptedCreditsReference,
    EditAction,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    McfSourceVersion,
    ReviewMcfDocument,
    ReviewSynchronization,
    ScanMcfDocument,
    ScanMode,
    Severity,
)
from tests.mcf_import_support import _parsed_document, _source_timing

FIXTURES = Path(__file__).parent / "fixtures" / "mcf"





def test_mcf_import_creates_one_atomic_ordered_aggregate_with_isolated_metadata():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        media = MediaItemRow(
            id="media",
            server_id="server",
            jellyfin_id="item",
            item_type="Episode",
            name="Catalog title",
        )
        db.add(media)
        db.commit()
        parsed = parse_mcf_content((FIXTURES / "valid-1.1.mcf").read_bytes())
        catalog = ReviewMcfDocument(
            title="Catalog title",
            year=2013,
            work_type="episode",
            season=5,
            episode=14,
            material_start_seconds=0,
            material_end_seconds=6000,
        )

        imported = repository.import_mcf_as_saved_review(
            db,
            media_item=media,
            resolved_local_path="/tmp/media.mkv",
            media_source_id="version-1",
            source_name="safe.mcf",
            review_name="Imported MCF",
            parsed=parsed,
            catalog_mcf_document=catalog,
            duration_seconds=6000,
            **_source_timing(parsed),
        )

        assert imported.source_version.value == "1.1.0"
        assert imported.scan.origin.value == "imported"
        assert imported.scan.scan_audio is True
        assert imported.scan.scan_video is True
        assert imported.scan.mcf_document == parsed.source_metadata
        assert imported.review.memberships[0].role.value == "both"
        assert [item.subcategory.value for item in imported.review.filters] == [
            "violence",
            "gambling",
            "drugs",
        ]
        assert [item.source for item in imported.review.filters] == [
            "visual",
            "visual",
            "visual",
        ]
        assert [item.edit_action for item in imported.review.filters] == [
            EditAction.SKIP,
            EditAction.SKIP,
            EditAction.SKIP,
        ]
        assert [item.mcf_channel for item in imported.review.filters] == [
            McfChannel.BOTH,
            McfChannel.BOTH,
            McfChannel.VIDEO,
        ]
        assert all(item.severity in set(Severity) for item in imported.review.filters)
        assert imported.review.filters[1].details == "Some comment"
        assert (
            imported.review.filters[1].start_seconds,
            imported.review.filters[1].end_seconds,
        ) == (
            imported.review.filters[2].start_seconds,
            imported.review.filters[2].end_seconds,
        )
        assert imported.review.mcf_document.title == "Ozymandias"
        assert imported.review.mcf_document is not imported.scan.mcf_document
        assert imported.notices[0].fields == ["title"]
        assert db.query(ScanFindingRow).count() == 3
        assert db.query(MediaItemExportRow).count() == 0


def test_mcf_import_maps_review_cues_and_preserves_source_evidence():
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
        parsed = McfParseResult(
            source_version=McfSourceVersion.V1_1_0,
            source_metadata=ScanMcfDocument(
                source_version=McfSourceVersion.V1_1_0,
                title="Source cut",
                material_start_seconds=10,
                material_end_seconds=110,
            ),
            entries=[
                McfCueEntry(
                    cue_index=0,
                    payload_index=0,
                    line_number=7,
                    start_seconds=30,
                    end_seconds=50,
                    category=McfCategoryFamily.VIOLENCE,
                    subcategory=McfCategory.violence,
                    severity=Severity.HIGH,
                    mcf_channel=McfChannel.VIDEO,
                )
            ],
        )

        imported = repository.import_mcf_as_saved_review(
            db,
            media_item=media,
            resolved_local_path="/tmp/media.mkv",
            media_source_id=None,
            source_name="mapped.mcf",
            review_name="Mapped",
            parsed=parsed,
            catalog_mcf_document=ReviewMcfDocument(title="Target cut"),
            duration_seconds=50,
            synchronization=ReviewSynchronization(
                start_seconds=0,
                end_seconds=50,
                end_mode="before_credits",
            ),
            mcf_material_end_seconds=50,
            accepted_credits=None,
        )

        source = db.query(ScanFindingRow).one()
        assert imported.scan.mcf_document == parsed.source_metadata
        assert (source.start_seconds, source.end_seconds) == (30, 50)
        assert imported.review.synchronization == ReviewSynchronization(
            start_seconds=0,
            end_seconds=50,
            end_mode="before_credits",
        )
        assert imported.review.mcf_document.material_start_seconds == 0
        assert imported.review.mcf_document.material_end_seconds == 50
        assert (
            imported.review.filters[0].start_seconds,
            imported.review.filters[0].end_seconds,
        ) == (10, 20)


@pytest.mark.parametrize("material_end", [None, 40.0])
def test_mcf_import_file_end_keeps_distinct_canonical_material_end(material_end):
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
        parsed = _parsed_document([McfChannel.VIDEO])

        imported = repository.import_mcf_as_saved_review(
            db,
            media_item=media,
            resolved_local_path="/tmp/media.mkv",
            media_source_id=None,
            source_name="file-end.mcf",
            review_name="File end",
            parsed=parsed,
            catalog_mcf_document=ReviewMcfDocument(title="Movie"),
            duration_seconds=50,
            synchronization=ReviewSynchronization(
                start_seconds=5,
                end_seconds=50,
                end_mode="file_end",
            ),
            mcf_material_end_seconds=material_end,
            accepted_credits=None,
        )

        assert imported.review.synchronization == ReviewSynchronization(
            start_seconds=5,
            end_seconds=50,
            end_mode="file_end",
        )
        assert imported.review.mcf_document.material_start_seconds == 5
        assert imported.review.mcf_document.material_end_seconds == material_end
        assert imported.review.filters[0].start_seconds == 9.5
        assert imported.review.filters[0].end_seconds == 14


def test_mcf_import_validates_and_atomically_attaches_accepted_credits(
    monkeypatch, tmp_path
):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        video_path = tmp_path / "movie.mkv"
        video_path.write_bytes(b"generated-test-video")
        monkeypatch.setattr(repository, "get_video_duration", lambda _path: 50.0)
        media = MediaItemRow(
            id="media",
            server_id="http://jellyfin.test",
            jellyfin_id="movie",
            item_type="Movie",
            name="Movie",
            path=str(video_path),
        )
        db.add(media)
        db.commit()
        configuration = default_configuration(qwen_model=None)
        credits_scan = repository.create_scan(
            db,
            media_item=media,
            resolved_local_path=str(video_path),
            duration_seconds=50,
            scan_audio=False,
            audio_model=None,
            audio_filter=None,
            scan_video=True,
            video_model=None,
            scan_mode=ScanMode.CREDITS_DETECTION,
            credits_detection_configuration=configuration,
        )
        result = detect_from_observations(
            duration_seconds=50,
            configuration=configuration,
            chapters=[ChapterMarker(40, "End Credits")],
        )
        repository.complete_credits_detection(
            db, scan_id=credits_scan.id, cache_key="a" * 64, result=result
        )
        candidate = result.candidates[0]
        parsed = _parsed_document([McfChannel.VIDEO])
        timing = {
            "synchronization": ReviewSynchronization(
                start_seconds=0,
                end_seconds=50,
                end_mode="file_end",
            ),
            "mcf_material_end_seconds": 42,
        }

        with pytest.raises(repository.ReviewMembershipError):
            repository.import_mcf_as_saved_review(
                db,
                media_item=media,
                resolved_local_path=str(video_path),
                media_source_id=None,
                source_name="invalid.mcf",
                review_name="Invalid",
                parsed=parsed,
                catalog_mcf_document=ReviewMcfDocument(title="Movie"),
                duration_seconds=50,
                accepted_credits=AcceptedCreditsReference(
                    scan_id=credits_scan.id,
                    candidate_id="forged",
                    accepted_boundary_seconds=42,
                ),
                **timing,
            )
        assert db.query(ScanRow).count() == 1
        assert db.query(SavedReviewRow).count() == 0

        original_commit = db.commit
        monkeypatch.setattr(
            db,
            "commit",
            lambda: (_ for _ in ()).throw(RuntimeError("injected commit failure")),
        )
        with pytest.raises(RuntimeError, match="injected commit failure"):
            repository.import_mcf_as_saved_review(
                db,
                media_item=media,
                resolved_local_path=str(video_path),
                media_source_id=None,
                source_name="rollback.mcf",
                review_name="Rollback",
                parsed=parsed,
                catalog_mcf_document=ReviewMcfDocument(title="Movie"),
                duration_seconds=50,
                accepted_credits=AcceptedCreditsReference(
                    scan_id=credits_scan.id,
                    candidate_id=candidate.id,
                    accepted_boundary_seconds=42,
                ),
                **timing,
            )
        monkeypatch.setattr(db, "commit", original_commit)
        assert db.query(ScanRow).count() == 1
        assert db.query(ScanFindingRow).count() == 1
        assert db.query(SavedReviewRow).count() == 0

        imported = repository.import_mcf_as_saved_review(
            db,
            media_item=media,
            resolved_local_path=str(video_path),
            media_source_id=None,
            source_name="accepted.mcf",
            review_name="Accepted",
            parsed=parsed,
            catalog_mcf_document=ReviewMcfDocument(title="Movie"),
            duration_seconds=50,
            accepted_credits=AcceptedCreditsReference(
                scan_id=credits_scan.id,
                candidate_id=candidate.id,
                accepted_boundary_seconds=42,
            ),
            **timing,
        )

        assert [(item.scan_id, item.role.value) for item in imported.review.memberships] == [
            (imported.scan.id, "video"),
            (credits_scan.id, "video"),
        ]
        accepted_filter = imported.review.filters[-1]
        assert accepted_filter.source_scan_id == credits_scan.id
        assert accepted_filter.start_seconds == 42
        assert accepted_filter.end_seconds == 50
        assert imported.review.accepted_credits.detected_boundary_seconds == 40
        assert imported.review.accepted_credits.accepted_boundary_seconds == 42
        assert db.get(ScanRow, imported.scan.id).video_hash == repository.compute_video_md5(
            str(video_path)
        )
        credits_source = (
            db.query(ScanFindingRow).filter_by(scan_id=credits_scan.id).one()
        )
        assert credits_source.start_seconds == 40


