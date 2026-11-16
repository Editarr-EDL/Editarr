from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import api, repository
from scanner.credits.credits_detector import (
    ChapterMarker,
    default_configuration,
    detect_from_observations,
)
from scanner.db import Base, MediaItemRow, ScanFindingRow
from scanner.formats.edl import parse_edl_content
from scanner.mcf import parse_mcf_content
from scanner.models import (
    AcceptedCreditsReference,
    EditAction,
    ExportFormat,
    McfCategoryFamily,
    NewManualReviewFilter,
    ReviewCreate,
    ReviewDuplicate,
    ReviewFilterInput,
    ReviewMcfDocument,
    ReviewSave,
    ReviewScanMembership,
    ReviewSynchronization,
    ScanMode,
    Severity,
)
from scanner.persistence.repository.review_deletion import PublishedReviewDeletionBlocked
from scanner.settings.filter_defaults import apply_filter_defaults

FIXTURES = Path(__file__).parent / "fixtures" / "mcf"
API_CONTRACT = Path(__file__).parents[1] / "docs" / "api-contract.md"


def test_documented_mcf_contract_matches_live_openapi():
    schema = api.app.openapi()
    documentation = API_CONTRACT.read_text()
    routes = {
        "/api/mcf-registry": {"get"},
        "/api/settings/filters": {"get", "put"},
        "/api/settings/filters/apply": {"post"},
        "/api/mcf-imports/upload": {"post"},
        "/api/reviews/{review_id}/retime": {"post"},
        "/api/credits-detections": {"post"},
        "/api/credits-detections/{scan_id}": {"get"},
        "/api/credits-detections/{scan_id}/model-consent": {"post"},
        "/api/credits-detections/{scan_id}/accept": {"post"},
        "/api/credits-detections/{scan_id}/clip": {"get"},
        "/api/reviews/{review_id}/mcf/preflight": {"get"},
        "/api/reviews/{review_id}/mcf": {"post"},
        "/api/reviews/{review_id}/edl": {"post"},
        "/api/library-items/{item_id}/exports": {"get"},
        "/api/library-items/{item_id}/exports/{format}": {"delete"},
    }
    for route, methods in routes.items():
        assert route in documentation
        assert methods <= schema["paths"][route].keys()

    definitions = schema["components"]["schemas"]
    expected_properties = {
        "ExportSummary": {
            "format",
            "present",
            "exported_at",
            "review_id",
            "exported_format_revision",
            "current",
            "outdated",
            "exported_scans",
            "exported_review",
        },
        "SavedReviewSummary": {
            "revision",
            "content_revision",
            "edl_content_revision",
            "mcf_content_revision",
            "exports",
        },
        "ScanReferences": {"scan_id", "active_exports", "reviews"},
        "ReviewExportResponse": {
            "format",
            "review_id",
            "exported_format_revision",
            "exported_at",
            "exported_scans",
            "exported_review",
        },
    }
    for model, properties in expected_properties.items():
        assert model in documentation
        assert properties <= definitions[model]["properties"].keys()
        assert all(f"`{property_name}`" in documentation for property_name in properties)

    assert "EdlStatus" not in definitions
    assert "ReviewEdlExport" not in definitions
    assert "/api/manual-files/edl" not in schema["paths"]


def test_fresh_database_persists_complete_mcf_and_edl_journey(tmp_path):
    database_path = tmp_path / "integration.db"
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"temporary test media")
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        media = MediaItemRow(
            id="media",
            server_id="server",
            jellyfin_id="item",
            item_type="Movie",
            name="Integration Movie",
            path=str(media_path),
        )
        db.add(media)
        db.commit()
        catalog = ReviewMcfDocument(
            title="Integration Movie",
            year=2026,
            work_type="movie",
            material_start_seconds=0,
            material_end_seconds=6000,
        )

        parsed_10 = parse_mcf_content((FIXTURES / "valid-1.0.mcf").read_bytes())
        imported_10 = repository.import_mcf_as_saved_review(
            db,
            media_item=media,
            resolved_local_path=str(media_path),
            media_source_id="version-1",
            source_name="version-1.0.mcf",
            review_name="MCF 1.0 import",
            parsed=parsed_10,
            catalog_mcf_document=catalog,
            duration_seconds=6000,
            synchronization=ReviewSynchronization(
                start_seconds=parsed_10.source_metadata.material_start_seconds,
                end_seconds=parsed_10.source_metadata.material_end_seconds,
                end_mode="before_credits",
            ),
            mcf_material_end_seconds=parsed_10.source_metadata.material_end_seconds,
            accepted_credits=None,
        )
        parsed_11 = parse_mcf_content((FIXTURES / "valid-1.1.mcf").read_bytes())
        imported_11 = repository.import_mcf_as_saved_review(
            db,
            media_item=media,
            resolved_local_path=str(media_path),
            media_source_id="version-1",
            source_name="version-1.1.mcf",
            review_name="MCF 1.1 import",
            parsed=parsed_11,
            catalog_mcf_document=catalog,
            duration_seconds=6000,
            synchronization=ReviewSynchronization(
                start_seconds=parsed_11.source_metadata.material_start_seconds,
                end_seconds=parsed_11.source_metadata.material_end_seconds,
                end_mode="before_credits",
            ),
            mcf_material_end_seconds=parsed_11.source_metadata.material_end_seconds,
            accepted_credits=None,
        )
        imported_edl = repository.import_edl_as_saved_review(
            db,
            media_item=media,
            resolved_local_path=str(media_path),
            media_source_id="version-1",
            source_name="legacy.edl",
            review_name="EDL import",
            duration_seconds=6000,
            parsed=parse_edl_content(b"3 4 1\n"),
            catalog_mcf_document=catalog,
        )
        assert imported_10.source_version.value == "1.0.0"
        assert imported_11.source_version.value == "1.1.0"
        assert imported_edl.review.filters[0].category is None

        review = repository.create_saved_review(
            db,
            media_item_id=media.id,
            media_source_id="version-1",
            data=ReviewCreate(
                name="Review from scan",
                memberships=[
                    ReviewScanMembership(scan_id=imported_11.scan.id, role="both")
                ],
            ),
        )
        assert repository.delete_saved_review(db, imported_11.review.id, 0)

        applied = apply_filter_defaults(
            [ReviewFilterInput(**item.model_dump()) for item in review.filters],
            repository.get_structured_filter_defaults_settings(db),
        )
        review = repository.save_saved_review(
            db,
            review.id,
            ReviewSave(
                revision=review.revision,
                name=review.name,
                memberships=review.memberships,
                filters=applied.filters,
                mcf_document=review.mcf_document,
                synchronization=review.synchronization,
            ),
        )
        assert all(item.severity == Severity.MEDIUM for item in review.filters)

        manual = repository.create_saved_review(
            db,
            media_item_id=media.id,
            media_source_id="version-1",
            data=ReviewCreate(
                name="Manual MCF filter",
                new_manual_filters=[
                    NewManualReviewFilter(
                        id=str(uuid4()),
                        source="audio",
                        category=McfCategoryFamily.LANGUAGE,
                        subcategory="swearing",
                        severity=None,
                        mcf_channel=None,
                        mcf_comment="Manual cue",
                        label="Manual language",
                        details=None,
                        group_key=None,
                        start_seconds=8,
                        end_seconds=9,
                        confidence=1,
                        edit_action=EditAction.MUTE,
                        selected=True,
                    )
                ],
            ),
            catalog_mcf_document=catalog,
        )
        assert manual.filters[0].severity == Severity.MEDIUM

        duplicate = repository.duplicate_saved_review(
            db,
            review.id,
            ReviewDuplicate(revision=review.revision, name="Isolated duplicate"),
        )
        duplicate_document = duplicate.mcf_document.model_copy(
            update={"title": "Duplicate title"}
        )
        duplicate = repository.save_saved_review(
            db,
            duplicate.id,
            ReviewSave(
                revision=duplicate.revision,
                name=duplicate.name,
                memberships=duplicate.memberships,
                filters=[
                    ReviewFilterInput(**item.model_dump()) for item in duplicate.filters
                ],
                mcf_document=duplicate_document,
                synchronization=duplicate.synchronization,
            ),
        )
        assert repository.get_saved_review(db, review.id).mcf_document.title != "Duplicate title"

        assert repository.preflight_saved_review_mcf(db, review.id).eligible is True
        repository.export_saved_review_edl(db, review.id, review.revision)
        repository.export_saved_review_mcf(db, review.id, review.revision)
        assert media_path.with_suffix(".edl").is_file()
        assert media_path.with_suffix(".mcf").is_file()

        changed_filters = [
            ReviewFilterInput(
                **(
                    item.model_dump()
                    | ({"label": "EDL revision change"} if index == 0 else {})
                )
            )
            for index, item in enumerate(review.filters)
        ]
        review = repository.save_saved_review(
            db,
            review.id,
            ReviewSave(
                revision=review.revision,
                name=review.name,
                memberships=review.memberships,
                filters=changed_filters,
                mcf_document=review.mcf_document,
                synchronization=review.synchronization,
            ),
        )
        assert review.exports.exports[ExportFormat.EDL].outdated is True
        assert review.exports.exports[ExportFormat.MCF].current is True
        review_id = review.id
        review_revision = review.revision
        scan_id = imported_11.scan.id
        duplicate_id = duplicate.id
        duplicate_revision = duplicate.revision

    engine.dispose()
    restarted_engine = create_engine(f"sqlite:///{database_path}")
    with Session(restarted_engine) as db:
        restarted = repository.get_saved_review(db, review_id)
        assert restarted is not None
        assert restarted.exports.exports[ExportFormat.EDL].outdated is True
        assert restarted.exports.exports[ExportFormat.MCF].current is True

        assert repository.delete_library_item_export(
            db,
            server_id="server",
            jellyfin_item_id="item",
            media_source_id="version-1",
            export_format=ExportFormat.MCF,
        )
        assert media_path.with_suffix(".edl").is_file()
        assert not media_path.with_suffix(".mcf").exists()

        assert repository.delete_saved_review(db, duplicate_id, duplicate_revision)
        with pytest.raises(PublishedReviewDeletionBlocked):
            repository.delete_saved_review(db, review_id, review_revision)
        retained = repository.media_item_export_status(
            db,
            server_id="server",
            jellyfin_item_id="item",
            media_source_id="version-1",
        ).exports[ExportFormat.EDL]
        assert retained.present is True
        assert retained.review_id == review_id
        assert retained.exported_scans[0].id == scan_id

        references = repository.get_scan_references(db, scan_id)
        assert references.active_exports == [ExportFormat.EDL]
        try:
            repository.delete_scan(db, scan_id)
        except repository.ScanReferencedError:
            pass
        else:
            raise AssertionError("Active EDL provenance must block scan deletion")

        assert repository.delete_library_item_export(
            db,
            server_id="server",
            jellyfin_item_id="item",
            media_source_id="version-1",
            export_format=ExportFormat.EDL,
        )
        assert repository.delete_saved_review(db, review_id, review_revision)
        assert repository.delete_scan(db, scan_id)

    restarted_engine.dispose()


def test_affine_import_with_edited_credits_exports_canonical_mcf_and_edl(tmp_path):
    media_path = tmp_path / "different-cut.mkv"
    media_path.write_bytes(b"generated-test-media")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        media = MediaItemRow(
            id="timed-media",
            server_id="http://jellyfin.test",
            jellyfin_id="different-cut",
            item_type="Movie",
            name="Different Cut",
            path=str(media_path),
        )
        db.add(media)
        db.commit()

        configuration = default_configuration(qwen_model=None)
        credits_scan = repository.create_scan(
            db,
            media_item=media,
            resolved_local_path=str(media_path),
            duration_seconds=120,
            scan_audio=False,
            audio_model=None,
            audio_filter=None,
            scan_video=True,
            video_model=None,
            scan_mode=ScanMode.CREDITS_DETECTION,
            credits_detection_configuration=configuration,
        )
        detection = detect_from_observations(
            duration_seconds=120,
            configuration=configuration,
            chapters=[ChapterMarker(90, "End Credits")],
        )
        repository.complete_credits_detection(
            db,
            scan_id=credits_scan.id,
            cache_key="a" * 64,
            result=detection,
        )
        candidate = detection.candidates[0]
        parsed = parse_mcf_content(
            b"WEBVTT MovieContentFilter 1.1.0\n\n"
            b"NOTE\nTITLE Different Cut\n\n"
            b"NOTE\nSTART 00:00:10.000\nEND 00:01:50.000\n\n"
            b"00:00:20.000 --> 00:00:30.000\nviolence=medium=video\n"
        )

        imported = repository.import_mcf_as_saved_review(
            db,
            media_item=media,
            resolved_local_path=str(media_path),
            media_source_id=None,
            source_name="different-cut.mcf",
            review_name="Synchronized with credits",
            parsed=parsed,
            catalog_mcf_document=ReviewMcfDocument(title="Different Cut"),
            duration_seconds=120,
            synchronization=ReviewSynchronization(
                start_seconds=0,
                end_seconds=120,
                end_mode="file_end",
            ),
            mcf_material_end_seconds=92,
            accepted_credits=AcceptedCreditsReference(
                scan_id=credits_scan.id,
                candidate_id=candidate.id,
                accepted_boundary_seconds=92,
            ),
        )

        source_cue = (
            db.query(ScanFindingRow)
            .filter_by(scan_id=imported.scan.id)
            .one()
        )
        credits_source = (
            db.query(ScanFindingRow)
            .filter_by(scan_id=credits_scan.id)
            .one()
        )
        assert (source_cue.start_seconds, source_cue.end_seconds) == (20, 30)
        mapped_filter = imported.review.filters[0]
        assert (mapped_filter.start_seconds, mapped_filter.end_seconds) == (
            12,
            24,
        )
        assert credits_source.start_seconds == 90
        assert imported.review.filters[-1].start_seconds == 92
        assert imported.review.filters[-1].end_seconds == 120
        assert imported.review.accepted_credits.detected_boundary_seconds == 90
        assert imported.review.accepted_credits.accepted_boundary_seconds == 92

        repository.export_saved_review_mcf(
            db, imported.review.id, imported.review.revision
        )
        repository.export_saved_review_edl(
            db, imported.review.id, imported.review.revision
        )

        mcf_bytes = media_path.with_suffix(".mcf").read_bytes()
        assert b"START 00:00:00.000\nEND 00:01:32.000" in mcf_bytes
        assert b"dispensable=medium=video # End Credits" in mcf_bytes
        edl_text = media_path.with_suffix(".edl").read_text()
        assert "92.000 120.000 3" in edl_text

    engine.dispose()
