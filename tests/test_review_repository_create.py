
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository
from scanner.db import (
    Base,
    MediaItemExportRow,
    MediaItemExportScanRow,
    MediaItemRow,
    ReviewMcfDocumentRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanMcfDocumentRow,
    ScanRow,
)
from scanner.models import (
    EditAction,
    ExportFormat,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    McfMetadataScanSeed,
    McfSourceVersion,
    NewManualReviewFilter,
    ReviewCreate,
    ReviewDuplicate,
    ReviewFilterInput,
    ReviewMcfDocument,
    ReviewRename,
    ReviewSave,
    ReviewScanMembership,
    Severity,
)
from tests.review_repository_support import review_db


def test_concurrent_review_saves_reject_stale_changed_and_unchanged_payloads(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review-concurrency.db'}")
    Base.metadata.create_all(engine)
    setup = Session(engine)
    setup.add_all(
        [
            MediaItemRow(
                id="media",
                server_id="server",
                jellyfin_id="item",
                item_type="Movie",
                name="Movie",
            ),
            ScanRow(
                id="scan",
                media_item_id="media",
                scan_number=1,
                source_path="/tmp/movie.mkv",
                resolved_local_path="/tmp/movie.mkv",
                status="complete",
                duration_seconds=100,
                scan_audio=True,
                scan_video=False,
            ),
        ]
    )
    setup.commit()
    setup.add(
        ScanFindingRow(
            scan_id="scan",
            category=McfCategoryFamily.LANGUAGE,
            subcategory="swearing",
            start_seconds=1,
            end_seconds=2,
            confidence=1,
            label="Source",
            source="audio",
            selected=True,
            edit_action="mute",
        )
    )
    setup.commit()
    created = repository.create_saved_review(
        setup,
        media_item_id="media",
        media_source_id=None,
        data=ReviewCreate(
            name="Review",
            memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
        ),
    )
    setup.close()

    first = Session(engine)
    second = Session(engine)
    try:
        first_copy = repository.get_saved_review(first, created.id)
        stale_copy = repository.get_saved_review(second, created.id)
        winning_filter = ReviewFilterInput(
            **(first_copy.filters[0].model_dump() | {"label": "Winner"})
        )
        winner = repository.save_saved_review(
            first,
            first_copy.id,
            ReviewSave(
                revision=0,
                name=first_copy.name,
                memberships=first_copy.memberships,
                filters=[winning_filter],
                mcf_document=first_copy.mcf_document,
                synchronization=first_copy.synchronization,
            ),
        )
        assert winner.revision == 1

        with pytest.raises(repository.ReviewRevisionConflict):
            repository.save_saved_review(
                second,
                stale_copy.id,
                ReviewSave(
                    revision=0,
                    name=stale_copy.name,
                    memberships=stale_copy.memberships,
                    filters=[
                        ReviewFilterInput(
                            **(
                                stale_copy.filters[0].model_dump()
                                | {"label": "Loser"}
                            )
                        )
                    ],
                    mcf_document=stale_copy.mcf_document,
                    synchronization=stale_copy.synchronization,
                ),
            )
        with pytest.raises(repository.ReviewRevisionConflict):
            repository.save_saved_review(
                second,
                stale_copy.id,
                ReviewSave(
                    revision=0,
                    name=stale_copy.name,
                    memberships=stale_copy.memberships,
                    filters=[
                        ReviewFilterInput(**stale_copy.filters[0].model_dump())
                    ],
                    mcf_document=stale_copy.mcf_document,
                    synchronization=stale_copy.synchronization,
                ),
            )
        second.expire_all()
        persisted = repository.get_saved_review(second, created.id)
        assert persisted.revision == 1
        assert persisted.filters[0].label == "Winner"
    finally:
        first.close()
        second.close()
        engine.dispose()


def test_review_snapshot_defaults_do_not_mutate_source_evidence():
    db = review_db()
    try:
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Defaults",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )

        assert created.filters[0].severity == Severity.MEDIUM
        assert created.filters[0].mcf_channel == McfChannel.AUDIO
        source = db.get(ScanFindingRow, 1)
        assert source.severity is None
        assert source.mcf_channel is None
    finally:
        db.close()


def test_manual_defaults_are_stored_on_new_evidence_and_review_copy(tmp_path):
    db = review_db()
    try:
        media = db.get(MediaItemRow, "media")
        media.path = str(tmp_path / "movie.mkv")
        (tmp_path / "movie.mkv").write_bytes(b"video")
        staged = NewManualReviewFilter(
            id="db7baf4d-9dae-4f70-92c1-4ae83fc8113d",
            source="visual",
            category=McfCategoryFamily.VIOLENCE,
            subcategory=McfCategory.violence,
            severity=None,
            mcf_channel=None,
            mcf_comment=None,
            label="Manual",
            details=None,
            group_key=None,
            start_seconds=1,
            end_seconds=2,
            confidence=1,
            edit_action=EditAction.SKIP,
        )

        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(name="Manual", new_manual_filters=[staged]),
        )

        assert created.filters[0].severity == Severity.MEDIUM
        assert created.filters[0].mcf_channel == McfChannel.VIDEO
        source = db.get(ScanFindingRow, created.filters[0].source_finding_id)
        assert source.severity == Severity.MEDIUM
        assert source.mcf_channel == McfChannel.VIDEO
    finally:
        db.close()


def test_review_metadata_uses_catalog_runtime_defaults_and_rejects_long_end():
    db = review_db()
    try:
        db.get(ScanRow, "scan").duration_seconds = 90
        db.commit()
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Metadata",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        assert created.mcf_document == ReviewMcfDocument(
            title="Movie",
            work_type="movie",
            material_start_seconds=0,
            material_end_seconds=None,
        )

        with pytest.raises(ValueError, match="exceed"):
            repository.save_saved_review(
                db,
                created.id,
                ReviewSave(
                    revision=created.revision,
                    name=created.name,
                    memberships=created.memberships,
                    filters=[ReviewFilterInput(**created.filters[0].model_dump())],
                    mcf_document=created.mcf_document.model_copy(
                        update={"material_end_seconds": 91}
                    ),
                    synchronization=created.synchronization,
                ),
            )
        assert repository.get_saved_review(db, created.id).revision == 0
    finally:
        db.close()


def test_conflicting_imported_metadata_requires_choice_and_duplicate_is_isolated():
    db = review_db()
    try:
        for number, title in ((2, "First"), (3, "Second")):
            scan = ScanRow(
                id=f"import-{number}",
                media_item_id="media",
                scan_number=number,
                name=title,
                origin="imported",
                source_path="/tmp/movie.mkv",
                resolved_local_path="/tmp/movie.mkv",
                duration_seconds=90,
                status="complete",
                scan_audio=True,
                scan_video=False,
            )
            scan.mcf_document = ScanMcfDocumentRow(
                source_version=McfSourceVersion.V1_1_0,
                title=title,
                material_start_seconds=1,
                material_end_seconds=89,
            )
            db.add(scan)
        db.commit()
        memberships = [
            ReviewScanMembership(scan_id="import-2", role="audio"),
            ReviewScanMembership(scan_id="import-3", role="audio"),
        ]
        catalog = ReviewMcfDocument(
            title="Catalog", year=2020, material_end_seconds=90
        )

        with pytest.raises(repository.McfMetadataSourceRequired) as error:
            repository.create_saved_review(
                db,
                media_item_id="media",
                media_source_id=None,
                data=ReviewCreate(name="Conflict", memberships=memberships),
                catalog_mcf_document=catalog,
            )
        assert [candidate.kind for candidate in error.value.candidates] == [
            "scan",
            "scan",
            "catalog",
        ]
        assert [candidate.scan_id for candidate in error.value.candidates] == [
            "import-2",
            "import-3",
            None,
        ]
        assert db.query(SavedReviewRow).count() == 0

        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Chosen",
                memberships=memberships,
                mcf_metadata_seed=McfMetadataScanSeed(
                    kind="scan", scan_id="import-3"
                ),
            ),
            catalog_mcf_document=catalog,
        )
        assert created.mcf_document.title == "Second"
        assert created.mcf_document.year == 2020
        duplicate = repository.duplicate_saved_review(
            db,
            created.id,
            ReviewDuplicate(revision=0, name="Copy"),
        )
        assert duplicate.mcf_document == created.mcf_document

        changed = duplicate.mcf_document.model_copy(update={"title": "Copy title"})
        repository.save_saved_review(
            db,
            duplicate.id,
            ReviewSave(
                revision=0,
                name=duplicate.name,
                memberships=duplicate.memberships,
                filters=[
                    ReviewFilterInput(**review_filter.model_dump())
                    for review_filter in duplicate.filters
                ],
                mcf_document=changed,
                synchronization=duplicate.synchronization,
            ),
        )
        assert repository.get_saved_review(db, created.id).mcf_document.title == "Second"
    finally:
        db.close()


def test_mcf_metadata_and_format_exports_convert_independently():
    db = review_db()
    try:
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Review",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        db.add(
            ScanMcfDocumentRow(
                scan_id="scan",
                source_version=McfSourceVersion.V1_0_0,
                title="Source",
                material_start_seconds=0,
                material_end_seconds=90,
            )
        )
        review_document = db.get(ReviewMcfDocumentRow, created.id)
        review_document.title = "Editable"
        review_document.material_start_seconds = 0
        review_document.material_end_seconds = 90
        for export_format in ExportFormat:
            db.add(
                MediaItemExportRow(
                    media_item_id="media",
                    format=export_format,
                    review_id=created.id,
                    exported_format_revision=0,
                    scans=[MediaItemExportScanRow(scan_id="scan", role="audio")],
                )
            )
        db.commit()
        db.expire_all()

        review = repository.get_saved_review(db, created.id)
        scan = repository.list_scan_history(db)[0]
        assert review.mcf_document.title == "Editable"
        assert scan.mcf_document.title == "Source"
        assert scan.mcf_document.source_version == McfSourceVersion.V1_0_0
        assert review.exports.exports[ExportFormat.EDL].current is True
        assert review.exports.exports[ExportFormat.MCF].current is True
        settings = repository.get_structured_filter_defaults_settings(db)
        assert settings.revision == 0
        assert len(settings.severity_defaults) == 105
        assert settings.severity_defaults[0].subcategory == "commercial"
        assert settings.severity_defaults[-1].subcategory == "abuse"
    finally:
        db.close()


def test_saved_review_rename_duplicate_references_and_delete():
    db = review_db()
    try:
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Original",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        renamed = repository.rename_saved_review(
            db, created.id, ReviewRename(revision=0, name="Renamed")
        )
        assert renamed.revision == 1
        assert renamed.content_revision == 0

        duplicate = repository.duplicate_saved_review(
            db, created.id, ReviewDuplicate(revision=1, name="Copy")
        )
        assert duplicate.revision == 0
        assert duplicate.filters[0].id != renamed.filters[0].id
        references = repository.get_scan_references(db, "scan")
        assert {reference.name for reference in references.reviews} == {"Renamed", "Copy"}

        assert repository.delete_saved_review(db, duplicate.id, 0)
        assert repository.get_saved_review(db, duplicate.id) is None
        assert repository.get_scan_references(db, "scan").reviews[0].name == "Renamed"
    finally:
        db.close()


def test_generic_review_lifecycle_preserves_derived_target_source():
    db = review_db()
    try:
        media = db.get(MediaItemRow, "media")
        media.server_id = "http://jellyfin.test"
        db.commit()
        created = repository.create_saved_review(
            db,
            media_item_id=media.id,
            media_source_id=None,
            data=ReviewCreate(name="Created"),
        )
        loaded = repository.get_saved_review(db, created.id)
        saved = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=created.revision,
                name="Saved",
                memberships=created.memberships,
                filters=[],
                mcf_document=created.mcf_document,
                synchronization=created.synchronization,
            ),
        )
        renamed = repository.rename_saved_review(
            db, created.id, ReviewRename(revision=saved.revision, name="Renamed")
        )
        duplicate = repository.duplicate_saved_review(
            db,
            created.id,
            ReviewDuplicate(revision=renamed.revision, name="Duplicate"),
        )

        assert loaded is not None
        assert saved is not None
        assert renamed is not None
        assert duplicate is not None
        for review in (created, loaded, saved, renamed, duplicate):
            assert review.target.source == "jellyfin"
            assert review.target.item_id == "item"
    finally:
        db.close()


